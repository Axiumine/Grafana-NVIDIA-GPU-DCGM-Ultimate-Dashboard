# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Tests for tools/check_queries.py against a local fake Prometheus."""

import json
import runpy
import sys

import pytest
from prometheus_stub import closed_port_url

from tools import check_queries


def _write_dashboard(tmp_path, dashboard: dict, name: str = "dash.json"):
    path = tmp_path / name
    path.write_text(json.dumps(dashboard))
    return path


class _FakeUrlopenResponse:
    """Minimal stand-in for the object urllib.request.urlopen() returns -- just
    enough (read() + context-manager protocol) to test the exact request
    check_queries builds without touching the network."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeUrlopenResponse:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _recording_urlopen(monkeypatch, body: bytes = b'{"status": "success", "data": []}') -> list:
    """Replaces urllib.request.urlopen with a stub that records every (url, kwargs)
    call instead of hitting the network, and returns the list those calls land in.
    Used to pin the exact request URL and timeout -- both otherwise unobservable
    through the fake Prometheus, which normalizes away things like a doubled slash
    or a stray unescaped path segment via its own path parsing."""
    calls = []

    def fake_urlopen(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeUrlopenResponse(body)

    monkeypatch.setattr(check_queries.urllib.request, "urlopen", fake_urlopen)
    return calls


# --------------------------------------------------------------------------- #
# load_textbox_defaults
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("dashboard", "expected"),
    [
        ({}, {}),
        ({"templating": {}}, {}),
        ({"templating": {"list": []}}, {}),
        ({"templating": {"list": [{"type": "query", "name": "gpu"}]}}, {}),
        (
            {"templating": {"list": [{"type": "textbox", "name": "foo", "current": {"value": "bar"}}]}},
            {"foo": "bar"},
        ),
        (
            {"templating": {"list": [{"type": "textbox", "name": "foo", "query": "defaultq"}]}},
            {"foo": "defaultq"},
        ),
        ({"templating": {"list": [{"type": "textbox", "name": "foo"}]}}, {"foo": ""}),
        # current.value takes precedence over query when both are present.
        (
            {
                "templating": {
                    "list": [{"type": "textbox", "name": "foo", "current": {"value": "bar"}, "query": "defaultq"}]
                }
            },
            {"foo": "bar"},
        ),
    ],
)
def test_load_textbox_defaults(dashboard, expected):
    assert check_queries.load_textbox_defaults(dashboard) == expected


# --------------------------------------------------------------------------- #
# fetch_a_real_gpu_uuid
# --------------------------------------------------------------------------- #


def test_fetch_a_real_gpu_uuid_returns_first_value(prometheus_stub):
    prometheus_stub.responses[("label", "UUID", "")] = {"status": "success", "data": ["uuid-1", "uuid-2"]}
    assert check_queries.fetch_a_real_gpu_uuid(prometheus_stub.url) == "uuid-1"


def test_fetch_a_real_gpu_uuid_falls_back_on_empty_data(prometheus_stub):
    prometheus_stub.responses[("label", "UUID", "")] = {"status": "success", "data": []}
    assert check_queries.fetch_a_real_gpu_uuid(prometheus_stub.url) == ".*"


def test_fetch_a_real_gpu_uuid_falls_back_on_bad_json(prometheus_stub):
    prometheus_stub.responses[("label", "UUID", "")] = "not json"
    assert check_queries.fetch_a_real_gpu_uuid(prometheus_stub.url) == ".*"


def test_fetch_a_real_gpu_uuid_falls_back_on_connection_error():
    assert check_queries.fetch_a_real_gpu_uuid(closed_port_url()) == ".*"


def test_fetch_a_real_gpu_uuid_strips_exactly_one_trailing_slash(monkeypatch):
    calls = _recording_urlopen(monkeypatch, b'{"status": "success", "data": ["real-uuid"]}')
    assert check_queries.fetch_a_real_gpu_uuid("http://prom.example/") == "real-uuid"
    assert calls == [("http://prom.example/api/v1/label/UUID/values", {"timeout": 10})]


def test_fetch_a_real_gpu_uuid_does_not_strip_trailing_non_slash_chars(monkeypatch):
    # Pins the exact "/" strip set (not a wider one that would also eat an "X") and
    # the exact timeout -- both invisible through the fake Prometheus, which would
    # 404 either way on the resulting mismatched path.
    calls = _recording_urlopen(monkeypatch, b'{"status": "success", "data": ["real-uuid"]}')
    check_queries.fetch_a_real_gpu_uuid("http://prom.example/X")
    assert calls == [("http://prom.example/X/api/v1/label/UUID/values", {"timeout": 10})]


# --------------------------------------------------------------------------- #
# label_values
# --------------------------------------------------------------------------- #


def test_label_values_returns_data_and_records_request(prometheus_stub):
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA", "nodeB"],
    }
    values = check_queries.label_values(prometheus_stub.url, "job", "DCGM_FI_DEV_GPU_UTIL")
    assert values == ["nodeA", "nodeB"]
    assert prometheus_stub.requests[-1] == {
        "path": "/api/v1/label/job/values",
        "params": {"match[]": "DCGM_FI_DEV_GPU_UTIL"},
    }


def test_label_values_missing_data_key_returns_empty_list(prometheus_stub):
    prometheus_stub.responses[("label", "job", "x")] = {"status": "success"}
    assert check_queries.label_values(prometheus_stub.url, "job", "x") == []


def test_label_values_returns_empty_list_on_error():
    assert check_queries.label_values(closed_port_url(), "job", "x") == []


def test_label_values_strips_exactly_one_trailing_slash_and_uses_10s_timeout(monkeypatch):
    calls = _recording_urlopen(monkeypatch)
    check_queries.label_values("http://prom.example/", "job", "up")
    assert calls == [("http://prom.example/api/v1/label/job/values?match%5B%5D=up", {"timeout": 10})]


def test_label_values_does_not_strip_trailing_non_slash_chars(monkeypatch):
    calls = _recording_urlopen(monkeypatch)
    check_queries.label_values("http://prom.example/X", "job", "up")
    assert calls == [("http://prom.example/X/api/v1/label/job/values?match%5B%5D=up", {"timeout": 10})]


def test_label_values_escapes_slash_in_label_name(monkeypatch):
    # safe="" must be passed through as-is -- the library default (safe="/") would
    # leave "/" unescaped, folding a label like "a/b" into an extra path segment.
    calls = _recording_urlopen(monkeypatch)
    check_queries.label_values("http://prom.example", "a/b", "up")
    assert calls == [("http://prom.example/api/v1/label/a%2Fb/values?match%5B%5D=up", {"timeout": 10})]


# An HTTP error status must fall back like any other failure -- and close the HTTPError:
# filterwarnings=error turns the ResourceWarning an unclosed one emits into a failure.
@pytest.mark.parametrize("status", [404, 500])
def test_label_lookups_fall_back_on_http_error_status(prometheus_stub, status):
    prometheus_stub.responses[("label", "UUID", "")] = (status, "nope")
    prometheus_stub.responses[("label", "job", "x")] = (status, {"status": "error", "error": "nope"})

    assert check_queries.fetch_a_real_gpu_uuid(prometheus_stub.url) == ".*"
    assert check_queries.label_values(prometheus_stub.url, "job", "x") == []
    assert [r["path"] for r in prometheus_stub.requests] == [
        "/api/v1/label/UUID/values",
        "/api/v1/label/job/values",
    ]


# --------------------------------------------------------------------------- #
# fetch_dcgm_job_instance_regex
# --------------------------------------------------------------------------- #


def test_fetch_dcgm_job_instance_regex_joins_real_values(prometheus_stub):
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA", "nodeB"],
    }
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~"nodeA|nodeB"}')] = {
        "status": "success",
        "data": ["hostA"],
    }
    assert check_queries.fetch_dcgm_job_instance_regex(prometheus_stub.url) == ("nodeA|nodeB", "hostA")


def test_fetch_dcgm_job_instance_regex_falls_back_when_jobs_empty(prometheus_stub):
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {"status": "success", "data": []}
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~".*"}')] = {
        "status": "success",
        "data": ["hostB"],
    }
    assert check_queries.fetch_dcgm_job_instance_regex(prometheus_stub.url) == (".*", "hostB")


def test_fetch_dcgm_job_instance_regex_joins_multiple_instances(prometheus_stub):
    # A single-instance fixture can't distinguish "|".join(instances) from a
    # hardcoded single-element result -- exercise 2+ real instances so the "|"
    # separator is load-bearing.
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA"],
    }
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~"nodeA"}')] = {
        "status": "success",
        "data": ["hostA", "hostB"],
    }
    assert check_queries.fetch_dcgm_job_instance_regex(prometheus_stub.url) == ("nodeA", "hostA|hostB")


def test_fetch_dcgm_job_instance_regex_falls_back_when_instances_empty(prometheus_stub):
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA"],
    }
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~"nodeA"}')] = {
        "status": "success",
        "data": [],
    }
    assert check_queries.fetch_dcgm_job_instance_regex(prometheus_stub.url) == ("nodeA", ".*")


# --------------------------------------------------------------------------- #
# substitute
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("expr", "textbox_defaults", "gpu_equals_value", "query_var_subs", "expected"),
    [
        ('DCGM_FI_DEV_GPU_UTIL{UUID="$gpu"}', {}, "abc-123", {}, 'DCGM_FI_DEV_GPU_UTIL{UUID="abc-123"}'),
        ('DCGM_FI_DEV_GPU_UTIL{UUID="${gpu}"}', {}, "abc-123", {}, 'DCGM_FI_DEV_GPU_UTIL{UUID="abc-123"}'),
        ("rate(x[$__rate_interval])", {}, ".*", {}, "rate(x[1m])"),
        # $__range_s must substitute before $__range -- else this becomes "x[1h_s]".
        ("x[$__range_s]", {}, ".*", {}, "x[3600]"),
        ("x[$__range]", {}, ".*", {}, "x[1h]"),
        ("x[$__interval]", {}, ".*", {}, "x[15s]"),
        ('foo{a="$mytb"}', {"mytb": "myval"}, ".*", {}, 'foo{a="myval"}'),
        ('foo{a="${mytb}"}', {"mytb": "myval"}, ".*", {}, 'foo{a="myval"}'),
        ('up{job=~"$job"}', {}, ".*", {"job": "a|b"}, 'up{job=~"a|b"}'),
        # $jobxyz must not be partially eaten by the $job substitution.
        ('up{job=~"$jobxyz"}', {}, ".*", {"job": "a|b"}, 'up{job=~"$jobxyz"}'),
        # Same, but with an uppercase continuation -- the boundary check must cover
        # the full [A-Za-z0-9_] class, not just the lowercase half of it.
        ('up{job=~"$jobXYZ"}', {}, ".*", {"job": "a|b"}, 'up{job=~"$jobXYZ"}'),
        # textbox_defaults takes precedence over query_var_subs for the same name.
        ('up{gpu=~"$gpu"}', {"gpu": "OVERRIDE"}, ".*", {"gpu": "fromquery"}, 'up{gpu=~"OVERRIDE"}'),
    ],
)
def test_substitute(expr, textbox_defaults, gpu_equals_value, query_var_subs, expected):
    assert check_queries.substitute(expr, textbox_defaults, gpu_equals_value, query_var_subs) == expected


def test_substitute_combines_everything():
    expr = 'rate(DCGM_FI_DEV_GPU_UTIL{UUID="$gpu", job=~"$job"}[$__rate_interval])'
    result = check_queries.substitute(expr, {}, "real-uuid", {"job": "n1|n2"})
    assert result == 'rate(DCGM_FI_DEV_GPU_UTIL{UUID="real-uuid", job=~"n1|n2"}[1m])'


# --------------------------------------------------------------------------- #
# iter_panels_with_context
# --------------------------------------------------------------------------- #


def test_iter_panels_with_context_flat():
    panels = [{"id": 1, "type": "timeseries"}, {"id": 2, "type": "table"}]
    assert list(check_queries.iter_panels_with_context(panels)) == [(panels[0], None), (panels[1], None)]


def test_iter_panels_with_context_row_sets_title():
    child = {"id": 10, "type": "timeseries"}
    row = {"id": 5, "type": "row", "title": "My Row", "panels": [child]}
    assert list(check_queries.iter_panels_with_context([row])) == [(child, "My Row")]


def test_iter_panels_with_context_row_without_panels_key_yields_nothing():
    row = {"id": 5, "type": "row", "title": "Empty Row"}
    assert list(check_queries.iter_panels_with_context([row])) == []


def test_iter_panels_with_context_nested_row_uses_innermost_title():
    grandchild = {"id": 20, "type": "stat"}
    inner_row = {"type": "row", "title": "Inner", "panels": [grandchild]}
    outer_row = {"type": "row", "title": "Outer", "panels": [inner_row]}
    assert list(check_queries.iter_panels_with_context([outer_row])) == [(grandchild, "Inner")]


def test_iter_panels_with_context_mixed_top_level_and_row():
    top = {"id": 1, "type": "stat"}
    child = {"id": 2, "type": "stat"}
    row = {"type": "row", "title": "R", "panels": [child]}
    assert list(check_queries.iter_panels_with_context([top, row])) == [(top, None), (child, "R")]


# --------------------------------------------------------------------------- #
# classify
# --------------------------------------------------------------------------- #


def test_classify_ok_instant_value(prometheus_stub):
    prometheus_stub.responses[("query", "up")] = {
        "status": "success",
        "data": {"result": [{"metric": {}, "value": [1700000000, "42"]}]},
    }
    assert check_queries.classify(prometheus_stub.url, "up") == ("OK", "n=1, sample=42")


def test_classify_ok_counts_multiple_series(prometheus_stub):
    # A single-series fixture can't distinguish `n = len(result)` from a hardcoded
    # `n = 1` -- exercise a real multi-series result so the count is load-bearing.
    prometheus_stub.responses[("query", "multi")] = {
        "status": "success",
        "data": {
            "result": [
                {"metric": {"gpu": "0"}, "value": [1, "1"]},
                {"metric": {"gpu": "1"}, "value": [1, "2"]},
                {"metric": {"gpu": "2"}, "value": [1, "3"]},
            ]
        },
    }
    assert check_queries.classify(prometheus_stub.url, "multi") == ("OK", "n=3, sample=1")


def test_classify_ok_matrix_uses_last_value(prometheus_stub):
    # A 2-point series can't distinguish values[-1] (last) from values[1] (second) --
    # they're the same element. Use 3 points so the "last, not second" is load-bearing.
    prometheus_stub.responses[("query", "up[5m]")] = {
        "status": "success",
        "data": {"result": [{"metric": {}, "values": [[1, "1"], [2, "5"], [3, "9"]]}]},
    }
    assert check_queries.classify(prometheus_stub.url, "up[5m]") == ("OK", "n=1, sample=9")


def test_classify_ok_result_without_value_or_values(prometheus_stub):
    prometheus_stub.responses[("query", "weird")] = {
        "status": "success",
        "data": {"result": [{"metric": {"x": "y"}}]},
    }
    assert check_queries.classify(prometheus_stub.url, "weird") == ("OK", "n=1, sample=None")


def test_classify_empty(prometheus_stub):
    prometheus_stub.responses[("query", "absent")] = {"status": "success", "data": {"result": []}}
    assert check_queries.classify(prometheus_stub.url, "absent") == ("EMPTY", "")


def test_classify_empty_when_data_key_missing_entirely(prometheus_stub):
    # body.get("data", {}) must fall back to a dict, not None -- else the chained
    # .get("result") below would blow up with an AttributeError instead of reading
    # honestly as EMPTY.
    prometheus_stub.responses[("query", "no_data")] = {"status": "success"}
    assert check_queries.classify(prometheus_stub.url, "no_data") == ("EMPTY", "")


def test_classify_soft_error_with_message(prometheus_stub):
    prometheus_stub.responses[("query", "bad")] = {"status": "error", "error": "boom"}
    assert check_queries.classify(prometheus_stub.url, "bad") == ("ERROR", "boom")


def test_classify_soft_error_without_message_field(prometheus_stub):
    body = {"status": "error"}
    prometheus_stub.responses[("query", "bad2")] = body
    status, detail = check_queries.classify(prometheus_stub.url, "bad2")
    assert status == "ERROR"
    assert detail == str(body)


def test_classify_http_error_with_json_body(prometheus_stub):
    prometheus_stub.responses[("query", "bad3")] = (
        400,
        {"status": "error", "errorType": "bad_data", "error": "parse error at char 1"},
    )
    assert check_queries.classify(prometheus_stub.url, "bad3") == ("ERROR", "parse error at char 1")


def test_classify_http_error_with_non_json_body(prometheus_stub):
    prometheus_stub.responses[("query", "bad4")] = (500, "internal explosion")
    status, detail = check_queries.classify(prometheus_stub.url, "bad4")
    assert status == "ERROR"
    assert "500" in detail


def test_classify_non_json_200_body(prometheus_stub):
    prometheus_stub.responses[("query", "bad5")] = "not json at all"
    status, detail = check_queries.classify(prometheus_stub.url, "bad5")
    assert status == "ERROR"
    assert detail == "Expecting value: line 1 column 1 (char 0)"


def test_classify_http_error_with_json_body_missing_error_key(prometheus_stub):
    # get("error", str(e)) must fall back to str(e), not silently drop the message.
    prometheus_stub.responses[("query", "bad6")] = (403, {"status": "error"})
    status, detail = check_queries.classify(prometheus_stub.url, "bad6")
    assert status == "ERROR"
    assert "403" in detail


def test_classify_connection_error():
    status, detail = check_queries.classify(closed_port_url(), "up")
    assert status == "ERROR"
    assert detail


def test_classify_strips_exactly_one_trailing_slash_and_uses_15s_timeout(monkeypatch):
    calls = _recording_urlopen(monkeypatch, b'{"status": "success", "data": {"result": []}}')
    check_queries.classify("http://prom.example/", "up")
    assert calls == [("http://prom.example/api/v1/query?query=up", {"timeout": 15})]


def test_classify_does_not_strip_trailing_non_slash_chars(monkeypatch):
    calls = _recording_urlopen(monkeypatch, b'{"status": "success", "data": {"result": []}}')
    check_queries.classify("http://prom.example/X", "up")
    assert calls == [("http://prom.example/X/api/v1/query?query=up", {"timeout": 15})]


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #


def test_main_ok_empty_error_and_exit_code(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {
        "panels": [
            {"id": 1, "title": "Panel OK", "type": "timeseries", "targets": [{"refId": "A", "expr": "metric_ok"}]},
            {
                "id": 2,
                "title": "Panel Empty",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": "metric_empty"}],
            },
            {
                "id": 3,
                "title": "Panel Error",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": "metric_error"}],
            },
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", "metric_ok")] = {
        "status": "success",
        "data": {"result": [{"metric": {}, "value": [1, "5"]}]},
    }
    prometheus_stub.responses[("query", "metric_empty")] = {"status": "success", "data": {"result": []}}
    prometheus_stub.responses[("query", "metric_error")] = (400, {"status": "error", "error": "nope"})

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    rc = check_queries.main()
    out, err = capsys.readouterr()

    assert rc == 1
    assert "panel 1 'Panel OK' [A]: OK(n=1, sample=5)" in out
    # The trailing "\n" matters: EMPTY's detail is "", so nothing -- not even an
    # empty "()" -- must be appended after the status.
    assert "panel 2 'Panel Empty' [A]: EMPTY\n" in out
    assert "panel 3 'Panel Error' [A]: ERROR(nope)" in out
    assert "expr: metric_error" in err
    # The expr dump is an ERROR-only diagnostic -- OK/EMPTY targets must not trigger it.
    assert "expr: metric_ok" not in err
    assert "expr: metric_empty" not in err
    assert "3 target(s) checked." in err


@pytest.mark.parametrize(("panels_arg", "expected_ids"), [("2", {2}), ("1,3", {1, 3}), ("2,", {2})])
def test_main_panels_filter(monkeypatch, tmp_path, prometheus_stub, capsys, panels_arg, expected_ids):
    dashboard = {
        "panels": [
            {"id": pid, "title": f"Panel {pid}", "type": "timeseries", "targets": [{"refId": "A", "expr": f"m{pid}"}]}
            for pid in (1, 2, 3)
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    for pid in (1, 2, 3):
        prometheus_stub.responses[("query", f"m{pid}")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(
        sys, "argv", ["check_queries.py", str(path), "--panels", panels_arg, "--prom", prometheus_stub.url]
    )
    rc = check_queries.main()
    out, _ = capsys.readouterr()

    assert rc == 0
    for pid in (1, 2, 3):
        line_present = f"panel {pid} 'Panel {pid}'" in out
        assert line_present == (pid in expected_ids)
    query_requests = [r for r in prometheus_stub.requests if r["path"] == "/api/v1/query"]
    assert len(query_requests) == len(expected_ids)


@pytest.mark.parametrize("flag", ["--prom", "--prom-url"])
def test_main_prom_url_alias(monkeypatch, tmp_path, prometheus_stub, capsys, flag):
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries", "targets": [{"refId": "A", "expr": "m"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", "m")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), flag, prometheus_stub.url])
    rc = check_queries.main()
    out, _ = capsys.readouterr()

    assert rc == 0
    assert "panel 1 'P' [A]: EMPTY" in out


def test_main_skips_targets_without_expr(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "P",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": "has_expr"}, {"refId": "B"}],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", "has_expr")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    rc = check_queries.main()
    out, err = capsys.readouterr()

    assert rc == 0
    assert "[A]: EMPTY" in out
    assert "[B]" not in out
    assert "1 target(s) checked." in err


def test_main_continues_past_a_target_without_expr_to_later_targets(monkeypatch, tmp_path, prometheus_stub, capsys):
    # Order matters here (missing expr FIRST): a `break` instead of `continue` would
    # also skip every later target in the same panel, not just the bad one.
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "P",
                "type": "timeseries",
                "targets": [{"refId": "A"}, {"refId": "B", "expr": "has_expr"}],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", "has_expr")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    rc = check_queries.main()
    out, err = capsys.readouterr()

    assert rc == 0
    assert "[B]: EMPTY" in out
    assert "1 target(s) checked." in err


def test_main_checked_count_is_exact(monkeypatch, tmp_path, prometheus_stub, capsys):
    # "3 target(s) checked." as a substring check can't tell +1 from -1 (a run that
    # decremented from 0 would print "-3 target(s) checked.", which still contains
    # that substring) -- so this pins the exact printed line instead.
    dashboard = {
        "panels": [
            {"id": pid, "title": f"P{pid}", "type": "timeseries", "targets": [{"refId": "A", "expr": f"m{pid}"}]}
            for pid in (1, 2)
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    for pid in (1, 2):
        prometheus_stub.responses[("query", f"m{pid}")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    check_queries.main()
    _, err = capsys.readouterr()

    assert err.strip().splitlines()[-1] == "2 target(s) checked."


def test_main_panel_without_title_key_uses_empty_string(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {"panels": [{"id": 1, "type": "timeseries", "targets": [{"refId": "A", "expr": "m"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", "m")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    check_queries.main()
    out, _ = capsys.readouterr()

    assert "panel 1 '' [A]: EMPTY" in out


def test_main_panel_without_targets_key_checks_nothing(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries"}]}
    path = _write_dashboard(tmp_path, dashboard)

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    rc = check_queries.main()
    _, err = capsys.readouterr()

    assert rc == 0
    assert "0 target(s) checked." in err


def test_main_dashboard_without_panels_key_checks_nothing(monkeypatch, tmp_path, prometheus_stub, capsys):
    path = _write_dashboard(tmp_path, {})

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    rc = check_queries.main()
    out, err = capsys.readouterr()

    assert rc == 0
    assert out == ""
    assert "0 target(s) checked." in err


def test_main_uses_default_prom_url_when_not_specified(monkeypatch, tmp_path, prometheus_stub, capsys):
    monkeypatch.setattr(check_queries, "DEFAULT_PROM_URL", prometheus_stub.url)
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries", "targets": [{"refId": "A", "expr": "m"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", "m")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path)])  # no --prom/--prom-url
    rc = check_queries.main()
    out, _ = capsys.readouterr()

    assert rc == 0
    assert "[A]: EMPTY" in out


def test_main_target_without_ref_id_uses_placeholder(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries", "targets": [{"expr": "no_ref_id"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", "no_ref_id")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    check_queries.main()
    out, _ = capsys.readouterr()

    assert "panel 1 'P' [?]: EMPTY" in out
    query_requests = [r for r in prometheus_stub.requests if r["path"] == "/api/v1/query"]
    assert len(query_requests) == 1


def test_main_reports_row_context(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {
        "panels": [
            {
                "type": "row",
                "title": "GPU Row",
                "panels": [
                    {"id": 9, "title": "Nested Panel", "type": "timeseries", "targets": [{"refId": "A", "expr": "n"}]}
                ],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", "n")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    check_queries.main()
    out, _ = capsys.readouterr()

    assert "panel 9 'Nested Panel' (row 'GPU Row') [A]: EMPTY" in out


def test_main_substitutes_textbox_default(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {
        "templating": {"list": [{"type": "textbox", "name": "myvar", "current": {"value": "myval"}}]},
        "panels": [
            {
                "id": 1,
                "title": "P",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": 'up{foo="$myvar"}'}],
            }
        ],
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", 'up{foo="myval"}')] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    rc = check_queries.main()
    out, _ = capsys.readouterr()

    assert rc == 0
    assert "[A]: EMPTY" in out
    query_requests = [r for r in prometheus_stub.requests if r["path"] == "/api/v1/query"]
    assert query_requests[-1]["params"]["query"] == 'up{foo="myval"}'


def test_main_substitutes_job_instance_gpu(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "Equality panel",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": 'DCGM_FI_DEV_GPU_UTIL{UUID="$gpu"}'}],
            },
            {
                "id": 2,
                "title": "Regex panel",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": 'up{job=~"$job", instance=~"$instance"}'}],
            },
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("label", "UUID", "")] = {"status": "success", "data": ["uuid-XYZ"]}
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA", "nodeB"],
    }
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~"nodeA|nodeB"}')] = {
        "status": "success",
        "data": ["hostA"],
    }
    prometheus_stub.responses[("query", 'DCGM_FI_DEV_GPU_UTIL{UUID="uuid-XYZ"}')] = {
        "status": "success",
        "data": {"result": [{"metric": {}, "value": [1, "1"]}]},
    }
    prometheus_stub.responses[("query", 'up{job=~"nodeA|nodeB", instance=~"hostA"}')] = {
        "status": "success",
        "data": {"result": [{"metric": {}, "value": [1, "1"]}]},
    }

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    rc = check_queries.main()
    out, _ = capsys.readouterr()

    assert rc == 0
    assert "panel 1 'Equality panel' [A]: OK" in out
    assert "panel 2 'Regex panel' [A]: OK" in out


def test_main_help_shows_default_prom_url(monkeypatch, capsys):
    # DEFAULT_PROM_URL feeds both the argparse default and this --help text; every
    # main() test below passes --prom explicitly, so without this, mutating the
    # constant's value would go undetected. The literal string (not
    # check_queries.DEFAULT_PROM_URL) keeps the check from moving with the mutant.
    monkeypatch.setattr(sys, "argv", ["check_queries.py", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        check_queries.main()
    out, _ = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "default: http://localhost:9090" in out
    # RawDescriptionHelpFormatter must be used, or argparse's default formatter
    # collapses the docstring into one reflowed paragraph -- a coincidental word-wrap
    # at the same width could still line-break at the same spot as the original, so
    # this checks a paragraph *break* (a blank line default reflow always erases)
    # rather than a single line break (which reflow can accidentally reproduce).
    assert "whether it returns data.\n\nUsage: uv run tools/check_queries.py" in out
    assert "comma-separated panel ids to restrict to" in out
    assert "XX" not in out
    # Both spellings of the flag must be registered as the *same* option (so they
    # share one dest and appear together here), not just one that happens to work
    # as an unambiguous abbreviation of the other.
    assert "--prom, --prom-url PROM" in out


def test_main_block_via_runpy(monkeypatch, tmp_path, prometheus_stub, project_root):
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries", "targets": [{"refId": "A", "expr": "m"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query", "m")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", ["check_queries.py", str(path), "--prom", prometheus_stub.url])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(project_root / "tools" / "check_queries.py"), run_name="__main__")

    assert exc_info.value.code == 0
