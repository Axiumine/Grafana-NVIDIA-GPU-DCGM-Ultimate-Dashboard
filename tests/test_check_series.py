# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Tests for tools/check_series.py against a local fake Prometheus."""

import datetime
import json
import runpy
import sys

import pytest
from prometheus_stub import closed_port_url

from tools import check_series


def _write_dashboard(tmp_path, dashboard: dict, name: str = "dash.json"):
    path = tmp_path / name
    path.write_text(json.dumps(dashboard))
    return path


class _FakeHTTPResponse:
    """Minimal `with urlopen(...) as resp: resp.read()` stand-in."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


def _capture_urlopen(monkeypatch, body: dict):
    """Replaces urllib.request.urlopen with a spy recording each call's exact
    (url, timeout) and returning `body` as JSON, bypassing prometheus_stub
    entirely. Needed only for asserting the precise URL string and timeout value
    check_series.py builds -- prometheus_stub's handler url-decodes the request
    path before recording it (see prometheus_stub.py's do_GET), so it cannot
    distinguish e.g. a trailing "//" from a single "/", or one timeout value
    from another (a local server answers instantly regardless of the timeout
    passed to urlopen)."""
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append((url, timeout))
        return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(check_series.urllib.request, "urlopen", fake_urlopen)
    return calls


# --------------------------------------------------------------------------- #
# parse_time
# --------------------------------------------------------------------------- #


def test_parse_time_epoch_int():
    assert check_series.parse_time("1700000000") == 1700000000.0


def test_parse_time_epoch_float():
    assert check_series.parse_time("1700000000.5") == 1700000000.5


def test_parse_time_rfc3339_with_offset():
    dt = datetime.datetime.fromisoformat("2026-01-01T00:00:00+02:00")
    assert check_series.parse_time("2026-01-01T00:00:00+02:00") == dt.timestamp()


def test_parse_time_rfc3339_naive_assumes_utc():
    dt = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
    assert check_series.parse_time("2026-01-01T00:00:00") == dt.timestamp()


def test_parse_time_bad_input_raises():
    with pytest.raises(ValueError, match=r"Invalid isoformat|invalid literal"):
        check_series.parse_time("not-a-date")


# --------------------------------------------------------------------------- #
# parse_step_seconds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("step", "expected"), [("30s", 30), ("5m", 300), ("2h", 7200), ("  30s  ", 30)])
def test_parse_step_seconds_valid(step, expected):
    assert check_series.parse_step_seconds(step) == expected


@pytest.mark.parametrize("step", ["5x", "abc", "10", "", "30sxyz"])
def test_parse_step_seconds_invalid_raises(step):
    with pytest.raises(ValueError, match="--step must look like"):
        check_series.parse_step_seconds(step)


# --------------------------------------------------------------------------- #
# load_textbox_defaults
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("dashboard", "expected"),
    [
        ({}, {}),
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
    assert check_series.load_textbox_defaults(dashboard) == expected


# --------------------------------------------------------------------------- #
# fetch_a_real_gpu_uuid / label_values / fetch_dcgm_job_instance_regex
# --------------------------------------------------------------------------- #


def test_fetch_a_real_gpu_uuid_returns_first_value(prometheus_stub):
    prometheus_stub.responses[("label", "UUID", "")] = {"status": "success", "data": ["uuid-1", "uuid-2"]}
    assert check_series.fetch_a_real_gpu_uuid(prometheus_stub.url) == "uuid-1"


def test_fetch_a_real_gpu_uuid_falls_back_on_empty_data(prometheus_stub):
    prometheus_stub.responses[("label", "UUID", "")] = {"status": "success", "data": []}
    assert check_series.fetch_a_real_gpu_uuid(prometheus_stub.url) == ".*"


def test_fetch_a_real_gpu_uuid_falls_back_on_connection_error():
    assert check_series.fetch_a_real_gpu_uuid(closed_port_url()) == ".*"


def test_fetch_a_real_gpu_uuid_builds_url_and_uses_10s_timeout(monkeypatch):
    # A trailing "X/" (not just "/") is required to tell rstrip("/") apart from a
    # mutant rstrip that also happens to strip a lone "/" (e.g. a char-class
    # rstrip): only a genuine rstrip("/") stops after the "/" and keeps the "X".
    calls = _capture_urlopen(monkeypatch, {"status": "success", "data": ["uuid-1"]})
    result = check_series.fetch_a_real_gpu_uuid("http://exampleX/")
    assert calls == [("http://exampleX/api/v1/label/UUID/values", 10)]
    assert result == "uuid-1"


def test_label_values_returns_data_and_records_request(prometheus_stub):
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA", "nodeB"],
    }
    values = check_series.label_values(prometheus_stub.url, "job", "DCGM_FI_DEV_GPU_UTIL")
    assert values == ["nodeA", "nodeB"]
    assert prometheus_stub.requests[-1] == {
        "path": "/api/v1/label/job/values",
        "params": {"match[]": "DCGM_FI_DEV_GPU_UTIL"},
    }


def test_label_values_missing_data_key_returns_empty_list(prometheus_stub):
    prometheus_stub.responses[("label", "job", "x")] = {"status": "success"}
    assert check_series.label_values(prometheus_stub.url, "job", "x") == []


def test_label_values_returns_empty_list_on_error():
    assert check_series.label_values(closed_port_url(), "job", "x") == []


def test_label_values_builds_url_and_uses_10s_timeout(monkeypatch):
    calls = _capture_urlopen(monkeypatch, {"status": "success", "data": ["v1"]})
    result = check_series.label_values("http://exampleX/", "job", "up")
    assert calls == [("http://exampleX/api/v1/label/job/values?match%5B%5D=up", 10)]
    assert result == ["v1"]


def test_label_values_escapes_slash_in_label_name(monkeypatch):
    # safe="" must be passed through as-is -- the library default (safe="/") would
    # leave "/" unescaped, folding a label like "a/b" into an extra path segment.
    calls = _capture_urlopen(monkeypatch, {"status": "success", "data": []})
    check_series.label_values("http://prom.example", "a/b", "up")
    assert calls == [("http://prom.example/api/v1/label/a%2Fb/values?match%5B%5D=up", 10)]


# An HTTP error status must fall back like any other failure -- and close the HTTPError:
# filterwarnings=error turns the ResourceWarning an unclosed one emits into a failure.
@pytest.mark.parametrize("status", [404, 500])
def test_label_lookups_fall_back_on_http_error_status(prometheus_stub, status):
    prometheus_stub.responses[("label", "UUID", "")] = (status, "nope")
    prometheus_stub.responses[("label", "job", "x")] = (status, {"status": "error", "error": "nope"})

    assert check_series.fetch_a_real_gpu_uuid(prometheus_stub.url) == ".*"
    assert check_series.label_values(prometheus_stub.url, "job", "x") == []
    assert [r["path"] for r in prometheus_stub.requests] == [
        "/api/v1/label/UUID/values",
        "/api/v1/label/job/values",
    ]


def test_fetch_dcgm_job_instance_regex_joins_real_values(prometheus_stub):
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA", "nodeB"],
    }
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~"nodeA|nodeB"}')] = {
        "status": "success",
        "data": ["hostA"],
    }
    assert check_series.fetch_dcgm_job_instance_regex(prometheus_stub.url) == ("nodeA|nodeB", "hostA")


def test_fetch_dcgm_job_instance_regex_falls_back_when_jobs_empty(prometheus_stub):
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {"status": "success", "data": []}
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~".*"}')] = {
        "status": "success",
        "data": ["hostB"],
    }
    assert check_series.fetch_dcgm_job_instance_regex(prometheus_stub.url) == (".*", "hostB")


def test_fetch_dcgm_job_instance_regex_joins_multiple_instances_with_pipe(prometheus_stub):
    # test_..._joins_real_values above only ever programs a single instance, so
    # "|".join(...) on it can't be told apart from a mutated join separator --
    # single-element join never uses the separator at all.
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA"],
    }
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~"nodeA"}')] = {
        "status": "success",
        "data": ["hostA", "hostB"],
    }
    assert check_series.fetch_dcgm_job_instance_regex(prometheus_stub.url) == ("nodeA", "hostA|hostB")


def test_fetch_dcgm_job_instance_regex_falls_back_when_instances_empty(prometheus_stub):
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA"],
    }
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~"nodeA"}')] = {
        "status": "success",
        "data": [],
    }
    assert check_series.fetch_dcgm_job_instance_regex(prometheus_stub.url) == ("nodeA", ".*")


# --------------------------------------------------------------------------- #
# substitute
# --------------------------------------------------------------------------- #


_BUILTINS = {"$__rate_interval": "2m", "$__range_s": "7200", "$__range": "7200s", "$__interval": "30s"}


@pytest.mark.parametrize(
    ("expr", "textbox_defaults", "gpu_equals_value", "query_var_subs", "expected"),
    [
        ('UUID="$gpu"', {}, "abc-123", {}, 'UUID="abc-123"'),
        ('UUID="${gpu}"', {}, "abc-123", {}, 'UUID="abc-123"'),
        ("rate(x[$__rate_interval])", {}, ".*", {}, "rate(x[2m])"),
        # $__range_s must substitute before $__range -- else this becomes "x[7200s_s]".
        ("x[$__range_s]", {}, ".*", {}, "x[7200]"),
        ("x[$__range]", {}, ".*", {}, "x[7200s]"),
        ("x[$__interval]", {}, ".*", {}, "x[30s]"),
        ('foo{a="$mytb"}', {"mytb": "myval"}, ".*", {}, 'foo{a="myval"}'),
        ('up{job=~"$job"}', {}, ".*", {"job": "a|b"}, 'up{job=~"a|b"}'),
        # Brace form of a query/textbox variable (not the UUID-specific one above,
        # which is substituted by a separate regex before this loop even runs).
        ('up{job=~"${job}"}', {}, ".*", {"job": "a|b"}, 'up{job=~"a|b"}'),
        ('up{job=~"$jobxyz"}', {}, ".*", {"job": "a|b"}, 'up{job=~"$jobxyz"}'),
        # Uppercase boundary char: "$job" must not match as a prefix of "$jobXYZ"
        # either -- the trailing-boundary check has to reject A-Z, not just a-z.
        ('up{job=~"$jobXYZ"}', {}, ".*", {"job": "a|b"}, 'up{job=~"$jobXYZ"}'),
        ('up{gpu=~"$gpu"}', {"gpu": "OVERRIDE"}, ".*", {"gpu": "fromquery"}, 'up{gpu=~"OVERRIDE"}'),
    ],
)
def test_substitute(expr, textbox_defaults, gpu_equals_value, query_var_subs, expected):
    result = check_series.substitute(expr, textbox_defaults, gpu_equals_value, _BUILTINS, query_var_subs)
    assert result == expected


# --------------------------------------------------------------------------- #
# iter_panels_with_context
# --------------------------------------------------------------------------- #


def test_iter_panels_with_context_flat():
    panels = [{"id": 1, "type": "timeseries"}, {"id": 2, "type": "table"}]
    assert list(check_series.iter_panels_with_context(panels)) == [(panels[0], None), (panels[1], None)]


def test_iter_panels_with_context_row_sets_title():
    child = {"id": 10, "type": "timeseries"}
    row = {"id": 5, "type": "row", "title": "My Row", "panels": [child]}
    assert list(check_series.iter_panels_with_context([row])) == [(child, "My Row")]


def test_iter_panels_with_context_row_without_panels_key_yields_nothing():
    row = {"id": 5, "type": "row", "title": "Empty Row"}
    assert list(check_series.iter_panels_with_context([row])) == []


def test_iter_panels_with_context_nested_row_uses_innermost_title():
    grandchild = {"id": 20, "type": "stat"}
    inner_row = {"type": "row", "title": "Inner", "panels": [grandchild]}
    outer_row = {"type": "row", "title": "Outer", "panels": [inner_row]}
    assert list(check_series.iter_panels_with_context([outer_row])) == [(grandchild, "Inner")]


# --------------------------------------------------------------------------- #
# render_legend / unresolved_labels
# --------------------------------------------------------------------------- #


def test_render_legend_no_format_uses_sorted_label_string():
    assert check_series.render_legend(None, {"job": "b", "instance": "a"}) == '{instance="a",job="b"}'


def test_render_legend_no_format_empty_metric():
    assert check_series.render_legend("", {}) == "{}"


def test_render_legend_substitutes_tokens():
    assert check_series.render_legend("{{instance}}", {"instance": "hostA"}) == "hostA"


def test_render_legend_missing_label_becomes_empty_string():
    assert check_series.render_legend("{{instance}}", {}) == ""


def test_render_legend_multiple_tokens_and_whitespace():
    legend_format = "{{ job }}/{{instance}}"
    assert check_series.render_legend(legend_format, {"job": "j", "instance": "i"}) == "j/i"


@pytest.mark.parametrize(
    ("legend_format", "metric", "expected"),
    [
        (None, {}, []),
        ("", {"instance": "x"}, []),
        ("{{instance}}", {"instance": "hostA"}, []),
        ("{{instance}}", {}, ["instance"]),
        ("{{instance}}", {"instance": ""}, ["instance"]),
        ("{{job}}/{{instance}}", {"job": "j"}, ["instance"]),
    ],
)
def test_unresolved_labels(legend_format, metric, expected):
    assert check_series.unresolved_labels(legend_format, metric) == expected


# --------------------------------------------------------------------------- #
# query_range
# --------------------------------------------------------------------------- #


def test_query_range_ok_records_request_params(prometheus_stub):
    prometheus_stub.responses[("query_range", "up")] = {
        "status": "success",
        "data": {"result": [{"metric": {"instance": "a"}}, {"metric": {"instance": "b"}}]},
    }
    status, detail = check_series.query_range(prometheus_stub.url, "up", 1000.0, 2000.0, 30)
    assert status == "OK"
    assert detail == [{"instance": "a"}, {"instance": "b"}]
    req = prometheus_stub.requests[-1]
    assert req["path"] == "/api/v1/query_range"
    assert req["params"] == {"query": "up", "start": "1000.000", "end": "2000.000", "step": "30"}


def test_query_range_empty(prometheus_stub):
    prometheus_stub.responses[("query_range", "absent")] = {"status": "success", "data": {"result": []}}
    assert check_series.query_range(prometheus_stub.url, "absent", 0.0, 1.0, 30) == ("EMPTY", [])


def test_query_range_soft_error_with_message(prometheus_stub):
    prometheus_stub.responses[("query_range", "bad")] = {"status": "error", "error": "boom"}
    assert check_series.query_range(prometheus_stub.url, "bad", 0.0, 1.0, 30) == ("ERROR", "boom")


def test_query_range_soft_error_without_message_field(prometheus_stub):
    body = {"status": "error"}
    prometheus_stub.responses[("query_range", "bad2")] = body
    status, detail = check_series.query_range(prometheus_stub.url, "bad2", 0.0, 1.0, 30)
    assert status == "ERROR"
    assert detail == str(body)


def test_query_range_http_error_with_json_body(prometheus_stub):
    prometheus_stub.responses[("query_range", "bad3")] = (400, {"status": "error", "error": "parse error"})
    assert check_series.query_range(prometheus_stub.url, "bad3", 0.0, 1.0, 30) == ("ERROR", "parse error")


def test_query_range_http_error_json_body_without_error_key_falls_back_to_str_e(prometheus_stub):
    # A JSON body that parses fine but has no "error" key: msg must fall back to
    # str(e) -- not None (get()'s "no default" default) and not the literal
    # string "None" (str(None)).
    prometheus_stub.responses[("query_range", "bad3b")] = (403, {"detail": "nope, no 'error' key here"})
    status, detail = check_series.query_range(prometheus_stub.url, "bad3b", 0.0, 1.0, 30)
    assert status == "ERROR"
    assert detail is not None
    assert "403" in detail


def test_query_range_http_error_with_non_json_body(prometheus_stub):
    prometheus_stub.responses[("query_range", "bad4")] = (500, "internal explosion")
    status, detail = check_series.query_range(prometheus_stub.url, "bad4", 0.0, 1.0, 30)
    assert status == "ERROR"
    assert "500" in detail


def test_query_range_non_json_200_body(prometheus_stub):
    prometheus_stub.responses[("query_range", "bad5")] = "not json at all"
    status, detail = check_series.query_range(prometheus_stub.url, "bad5", 0.0, 1.0, 30)
    assert status == "ERROR"
    assert detail == "Expecting value: line 1 column 1 (char 0)"


def test_query_range_connection_error():
    status, detail = check_series.query_range(closed_port_url(), "up", 0.0, 1.0, 30)
    assert status == "ERROR"
    assert detail


def test_query_range_missing_data_key_is_empty(prometheus_stub):
    # "status": "success" but no "data" key at all (Prometheus always sends one,
    # but the code must not crash if it didn't): must fall back to EMPTY, not
    # raise (a None default for "data" would crash on the chained .get("result")).
    prometheus_stub.responses[("query_range", "nodata")] = {"status": "success"}
    assert check_series.query_range(prometheus_stub.url, "nodata", 0.0, 1.0, 30) == ("EMPTY", [])


def test_query_range_result_item_without_metric_key_defaults_to_empty_dict(prometheus_stub):
    prometheus_stub.responses[("query_range", "nometric")] = {
        "status": "success",
        "data": {"result": [{}]},
    }
    assert check_series.query_range(prometheus_stub.url, "nometric", 0.0, 1.0, 30) == ("OK", [{}])


def test_query_range_builds_url_and_uses_30s_timeout(monkeypatch):
    calls = _capture_urlopen(monkeypatch, {"status": "success", "data": {"result": []}})
    status, detail = check_series.query_range("http://exampleX/", "up", 1000.0, 2000.0, 30)
    assert calls == [("http://exampleX/api/v1/query_range?query=up&start=1000.000&end=2000.000&step=30", 30)]
    assert (status, detail) == ("EMPTY", [])


# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #

_START = "2026-01-01T00:00:00+00:00"
_END = "2026-01-01T01:00:00+00:00"


def _base_argv(path, prom_url, *extra):
    return ["check_series.py", str(path), "--prom", prom_url, "--start", _START, "--end", _END, *extra]


def test_main_prom_default_used_when_omitted(monkeypatch, tmp_path, prometheus_stub, capsys):
    # Point DEFAULT_PROM_URL itself at the fake server rather than using the real
    # module default (a real, unreachable "http://localhost:9090"): main() prints
    # "prom={args.prom}" regardless of whether any query ever succeeds, so this
    # stays deterministic without depending on what (if anything) is listening on
    # the real machine's port 9090.
    monkeypatch.setattr(check_series, "DEFAULT_PROM_URL", prometheus_stub.url)
    dashboard = {"panels": []}
    path = _write_dashboard(tmp_path, dashboard)
    monkeypatch.setattr(sys, "argv", ["check_series.py", str(path), "--start", _START, "--end", _END])
    rc = check_series.main()
    _, err = capsys.readouterr()
    assert rc == 0
    assert f"prom={prometheus_stub.url}" in err


def test_main_dashboard_without_panels_key_checks_nothing(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {}  # no "panels" key at all, unlike every other test's {"panels": [...]}
    path = _write_dashboard(tmp_path, dashboard)
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    _, err = capsys.readouterr()
    assert rc == 0
    assert "0 target(s) checked" in err


def test_main_end_before_start_errors_without_querying(monkeypatch, tmp_path, capsys):
    dashboard = {"panels": []}
    path = _write_dashboard(tmp_path, dashboard)
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_series.py", str(path), "--prom", "http://127.0.0.1:1", "--start", _END, "--end", _START],
    )
    rc = check_series.main()
    _, err = capsys.readouterr()
    assert rc == 2
    assert "error: --end" in err


def test_main_end_equal_start_errors(monkeypatch, tmp_path, capsys):
    # Boundary case for `if end <= start:` -- a strictly-less-than test alone can't
    # distinguish that from `if end < start:`.
    dashboard = {"panels": []}
    path = _write_dashboard(tmp_path, dashboard)
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_series.py", str(path), "--prom", "http://127.0.0.1:1", "--start", _START, "--end", _START],
    )
    rc = check_series.main()
    _, err = capsys.readouterr()
    assert rc == 2
    assert "error: --end" in err


def test_main_bad_step_raises(monkeypatch, tmp_path, prometheus_stub):
    dashboard = {"panels": []}
    path = _write_dashboard(tmp_path, dashboard)
    monkeypatch.setattr(sys, "argv", [*_base_argv(path, prometheus_stub.url, "--step", "5x")])
    with pytest.raises(ValueError, match="--step must look like"):
        check_series.main()


def test_main_ok_no_duplicate_no_legend_format(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "Table panel",
                "type": "table",
                "targets": [{"refId": "A", "expr": "metric_ok"}],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "metric_ok")] = {
        "status": "success",
        "data": {"result": [{"metric": {"instance": "a"}}, {"metric": {"instance": "b"}}]},
    }
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    out, err = capsys.readouterr()

    assert rc == 0
    assert "panel 1 'Table panel' [A]: OK(n=2)" in out
    assert "DUPLICATE" not in out
    assert "no ERROR, no DUPLICATE, 0 legend(s)" in err


def test_main_empty(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {
        "panels": [{"id": 1, "title": "P", "type": "timeseries", "targets": [{"refId": "A", "expr": "absent"}]}]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "absent")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "panel 1 'P' [A]: EMPTY" in out


def test_main_error(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {
        "panels": [
            {"id": 1, "title": "P", "type": "timeseries", "targets": [{"refId": "A", "expr": "bad"}]},
            {"id": 2, "title": "OK panel", "type": "timeseries", "targets": [{"refId": "A", "expr": "good"}]},
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "bad")] = (400, {"status": "error", "error": "nope"})
    prometheus_stub.responses[("query_range", "good")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    out, err = capsys.readouterr()
    assert rc == 1
    assert "panel 1 'P' [A]: ERROR(nope)" in out
    assert "expr: bad" in err
    # Trailing ", " pins the exact literal, not just a same-substring superstring
    # (a mutant that wraps the literal, e.g. "XXERRORs presentXX", would still
    # contain the bare "ERRORs present" substring but not this exact framing).
    assert "ERRORs present, " in err
    # The expr dump is an ERROR-only diagnostic -- the OK target must not trigger it.
    assert "expr: good" not in err


def test_main_duplicate_legend(monkeypatch, tmp_path, prometheus_stub, capsys):
    """A static (token-free) legendFormat collapses distinct series under one legend --
    this is the exact bug tools/check_series.py exists to catch."""
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "GPU Util",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": "dup_metric", "legendFormat": "GPU Utilization"}],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "dup_metric")] = {
        "status": "success",
        "data": {"result": [{"metric": {"UUID": "gpu-1"}}, {"metric": {"UUID": "gpu-2"}}]},
    }
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    out, err = capsys.readouterr()
    assert rc == 1
    # The full status string: "OK(n=2)" must still be *prepended* to (not
    # replaced by) the DUPLICATE note.
    assert "OK(n=2) DUPLICATE: 'GPU Utilization' x2" in out
    assert "DUPLICATEs present, " in err


def test_main_duplicate_legend_join_separator_with_two_groups(monkeypatch, tmp_path, prometheus_stub, capsys):
    # test_main_duplicate_legend above has only one duplicate group, so
    # "; ".join(...) on it never exercises the separator (a single-element join
    # ignores it entirely). Two groups, in a deterministic (insertion) order, do.
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "P",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": "dup2", "legendFormat": "{{a}}-{{b}}"}],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "dup2")] = {
        "status": "success",
        "data": {
            "result": [
                {"metric": {"a": "1", "b": "x"}},
                {"metric": {"a": "1", "b": "x"}},
                {"metric": {"a": "2", "b": "y"}},
                {"metric": {"a": "2", "b": "y"}},
            ]
        },
    }
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    check_series.main()
    out, _ = capsys.readouterr()
    assert "DUPLICATE: '1-x' x2; '2-y' x2" in out


def test_main_empty_label_line(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "P",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": "m", "legendFormat": "{{gpu}}"}],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "m")] = {
        "status": "success",
        "data": {"result": [{"metric": {"instance": "a"}}]},
    }
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    out, err = capsys.readouterr()
    assert rc == 0
    assert "empty-label: '' missing ['gpu']" in out
    assert "1 legend(s) with an unresolved/empty label." in err


def test_main_empty_label_lines_accumulate_across_targets(monkeypatch, tmp_path, prometheus_stub, capsys):
    # test_main_empty_label_line above has only one contributing target, so it
    # can't tell "+=" apart from a plain "=" (both land on 1). Two targets, each
    # contributing one empty-label line, must sum to 2.
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "P1",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": "m1", "legendFormat": "{{gpu}}"}],
            },
            {
                "id": 2,
                "title": "P2",
                "type": "timeseries",
                "targets": [{"refId": "A", "expr": "m2", "legendFormat": "{{gpu}}"}],
            },
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "m1")] = {
        "status": "success",
        "data": {"result": [{"metric": {"instance": "a"}}]},
    }
    prometheus_stub.responses[("query_range", "m2")] = {
        "status": "success",
        "data": {"result": [{"metric": {"instance": "b"}}]},
    }
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    check_series.main()
    _, err = capsys.readouterr()
    # ", 2 legend(s)..." (comma-space immediately before the digit) rules out a
    # "-=" mutant, whose "-2" would otherwise leave "2 legend(s) with an
    # unresolved/empty label." present as a same-line substring.
    assert ", 2 legend(s) with an unresolved/empty label." in err


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
        prometheus_stub.responses[("query_range", f"m{pid}")] = {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url, "--panels", panels_arg))
    rc = check_series.main()
    out, _ = capsys.readouterr()

    assert rc == 0
    for pid in (1, 2, 3):
        assert (f"panel {pid} 'Panel {pid}'" in out) == (pid in expected_ids)
    range_requests = [r for r in prometheus_stub.requests if r["path"] == "/api/v1/query_range"]
    assert len(range_requests) == len(expected_ids)


@pytest.mark.parametrize("flag", ["--prom", "--prom-url"])
def test_main_prom_url_alias(monkeypatch, tmp_path, prometheus_stub, capsys, flag):
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries", "targets": [{"refId": "A", "expr": "m"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "m")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(
        sys, "argv", ["check_series.py", str(path), flag, prometheus_stub.url, "--start", _START, "--end", _END]
    )
    rc = check_series.main()
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
    prometheus_stub.responses[("query_range", "has_expr")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    out, err = capsys.readouterr()
    assert rc == 0
    assert "[A]: EMPTY" in out
    assert "[B]" not in out
    assert "1 target(s) checked" in err
    range_requests = [r for r in prometheus_stub.requests if r["path"] == "/api/v1/query_range"]
    assert len(range_requests) == 1


def test_main_no_expr_target_does_not_stop_later_targets(monkeypatch, tmp_path, prometheus_stub, capsys):
    # Unlike test_main_skips_targets_without_expr above (the expr-less target is
    # last, so continue-vs-break can't be told apart), this puts it FIRST so a
    # `break` would wrongly skip the valid target that follows it.
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
    prometheus_stub.responses[("query_range", "has_expr")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    check_series.main()
    out, err = capsys.readouterr()
    assert "[B]: EMPTY" in out
    assert "1 target(s) checked" in err


def test_main_checked_counter_sums_across_multiple_targets(monkeypatch, tmp_path, prometheus_stub, capsys):
    # test_main_error above never checks the "N target(s) checked" text and only
    # has 1 valid-expr target per panel, so it can't tell "checked += 1" apart
    # from "checked = 1" (both land on the same count with a single target) or
    # from "checked -= 1". Three targets in ONE panel, with a mix of statuses
    # (also exercises the EMPTY/ERROR branches' `continue` not stopping the loop).
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "P",
                "type": "timeseries",
                "targets": [
                    {"refId": "A", "expr": "bad"},
                    {"refId": "B", "expr": "absent"},
                    {"refId": "C", "expr": "ok"},
                ],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "bad")] = (400, {"status": "error", "error": "nope"})
    prometheus_stub.responses[("query_range", "absent")] = {"status": "success", "data": {"result": []}}
    prometheus_stub.responses[("query_range", "ok")] = {
        "status": "success",
        "data": {"result": [{"metric": {"instance": "a"}}]},
    }
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    out, err = capsys.readouterr()
    assert rc == 1
    assert "[A]: ERROR(nope)" in out
    assert "[B]: EMPTY" in out
    assert "[C]: OK(n=1)" in out
    # Leading "\n3" (the literal newline the print's f-string starts with) rules
    # out a "-=" mutant, whose "-3" would otherwise leave "3 target(s) checked"
    # present as a same-line substring.
    assert "\n3 target(s) checked, " in err


def test_main_panel_without_title_uses_empty_string(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {"panels": [{"id": 1, "type": "timeseries", "targets": [{"refId": "A", "expr": "m"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "m")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    check_series.main()
    out, _ = capsys.readouterr()
    assert "panel 1 '' [A]: EMPTY" in out


def test_main_panel_without_targets_key_is_skipped(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries"}]}
    path = _write_dashboard(tmp_path, dashboard)
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    _, err = capsys.readouterr()
    assert rc == 0
    assert "0 target(s) checked" in err


def test_main_target_without_ref_id_uses_placeholder(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries", "targets": [{"expr": "no_ref_id"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "no_ref_id")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    check_series.main()
    out, _ = capsys.readouterr()
    assert "panel 1 'P' [?]: EMPTY" in out


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
    prometheus_stub.responses[("query_range", "n")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    check_series.main()
    out, _ = capsys.readouterr()
    assert "panel 9 'Nested Panel' (row 'GPU Row') [A]: EMPTY" in out


def test_main_builds_range_dependent_builtins_from_args(monkeypatch, tmp_path, prometheus_stub):
    # main() computes $__range_s/$__range/$__interval from --start/--end/--step
    # itself (unlike check_queries.py, where these are fixed module constants);
    # every other test exercises substitute() with a hand-built builtins dict, so
    # this is the only test asserting main()'s own construction of those values
    # against the exact query string Prometheus receives.
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "P",
                "type": "timeseries",
                "targets": [
                    {
                        "refId": "A",
                        "expr": "rate(x[$__rate_interval])[$__range_s:$__interval] offset $__range",
                    }
                ],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    expected_expr = "rate(x[2m])[3600:30s] offset 3600s"
    prometheus_stub.responses[("query_range", expected_expr)] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    rc = check_series.main()
    range_requests = [r for r in prometheus_stub.requests if r["path"] == "/api/v1/query_range"]
    assert rc == 0
    assert range_requests[-1]["params"]["query"] == expected_expr


def test_main_substitutes_job_instance_and_gpu_query_vars(monkeypatch, tmp_path, prometheus_stub):
    """main() wires the live job/instance/UUID lookups into query_var_subs and
    gpu_equals_value itself (every other test either leaves $job/$instance/$gpu
    out of the expr, or exercises substitute() directly with a hand-built dict),
    so this is the only test asserting end to end that $job/$instance resolve to
    the real fetch_dcgm_job_instance_regex() values, that UUID="$gpu" resolves to
    the real fetch_a_real_gpu_uuid() value, and that a *plain* $gpu (outside a
    UUID="..." match, e.g. row types other than row B's) falls back to the
    literal ".*" query_var_subs sets for it."""
    dashboard = {
        "panels": [
            {
                "id": 1,
                "title": "P",
                "type": "timeseries",
                "targets": [
                    {"refId": "A", "expr": 'up{job=~"$job", instance=~"$instance", UUID="$gpu"}'},
                    {"refId": "B", "expr": 'foo{gpu=~"$gpu"}'},
                ],
            }
        ]
    }
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("label", "job", "DCGM_FI_DEV_GPU_UTIL")] = {
        "status": "success",
        "data": ["nodeA", "nodeB"],
    }
    prometheus_stub.responses[("label", "instance", 'DCGM_FI_DEV_GPU_UTIL{job=~"nodeA|nodeB"}')] = {
        "status": "success",
        "data": ["hostA"],
    }
    prometheus_stub.responses[("label", "UUID", "")] = {"status": "success", "data": ["gpu-xyz"]}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    check_series.main()
    range_requests = [r for r in prometheus_stub.requests if r["path"] == "/api/v1/query_range"]
    queries = [r["params"]["query"] for r in range_requests]
    assert 'up{job=~"nodeA|nodeB", instance=~"hostA", UUID="gpu-xyz"}' in queries
    assert 'foo{gpu=~".*"}' in queries


def test_main_custom_step_is_sent(monkeypatch, tmp_path, prometheus_stub):
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries", "targets": [{"refId": "A", "expr": "m"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "m")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url, "--step", "2m"))
    check_series.main()
    range_requests = [r for r in prometheus_stub.requests if r["path"] == "/api/v1/query_range"]
    assert range_requests[-1]["params"]["step"] == "120"


def test_main_default_start_used_when_omitted(monkeypatch, tmp_path, prometheus_stub, capsys):
    dashboard = {"panels": []}
    path = _write_dashboard(tmp_path, dashboard)
    monkeypatch.setattr(
        sys, "argv", ["check_series.py", str(path), "--prom", prometheus_stub.url, "--end", "2026-09-27T00:00:00+00:00"]
    )
    check_series.main()
    _, err = capsys.readouterr()
    # Literal expected value (not derived from check_series.DEFAULT_START) so a
    # mutation of that constant can't drag its own expectation along with it.
    assert "# window: 2026-09-26T01:30:00+00:00 .." in err


def test_main_default_end_is_now(monkeypatch, tmp_path, prometheus_stub, capsys):
    fixed_now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    captured_tzs = []

    class _FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            captured_tzs.append(tz)
            return fixed_now

    monkeypatch.setattr(check_series.datetime, "datetime", _FrozenDatetime)
    dashboard = {"panels": []}
    path = _write_dashboard(tmp_path, dashboard)
    monkeypatch.setattr(sys, "argv", ["check_series.py", str(path), "--prom", prometheus_stub.url, "--start", _START])
    check_series.main()
    _, err = capsys.readouterr()
    expected_seconds = fixed_now.timestamp() - check_series.parse_time(_START)
    assert f"({expected_seconds:.0f}s)" in err
    # main() must ask for UTC specifically, not the system/naive local clock.
    assert captured_tzs == [datetime.UTC]


def test_main_window_line_uses_utc_for_both_start_and_end(monkeypatch, tmp_path, prometheus_stub):
    # The window line's `datetime.fromtimestamp(..., datetime.UTC)` calls are
    # otherwise never distinguished from a `tz=None` (naive/local) one: on a
    # machine whose local timezone happens to be UTC the rendered text would be
    # identical either way, so this captures the actual tz argument instead of
    # comparing rendered strings.
    #
    # Patching datetime.datetime is process-global (check_series.datetime *is*
    # the stdlib datetime module), so it also catches unrelated fromtimestamp()
    # calls -- notably http.server's own Date-header formatting on every request
    # the background fake-Prometheus thread serves. Filtering to our own known
    # start/end epoch values (a 2026-01-01 fixture date, far from the real
    # wall-clock "now" those Date headers use) discards that noise.
    captured = []
    real_fromtimestamp = datetime.datetime.fromtimestamp
    expected_start = check_series.parse_time(_START)
    expected_end = check_series.parse_time(_END)

    class _SpyDatetime(datetime.datetime):
        @classmethod
        def fromtimestamp(cls, ts, tz=None):
            if ts in (expected_start, expected_end):
                captured.append((ts, tz))
            return real_fromtimestamp(ts, tz)

    monkeypatch.setattr(check_series.datetime, "datetime", _SpyDatetime)
    dashboard = {"panels": []}
    path = _write_dashboard(tmp_path, dashboard)
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    check_series.main()
    assert captured == [(expected_start, datetime.UTC), (expected_end, datetime.UTC)]


def test_main_help_shows_defaults(monkeypatch, capsys):
    # DEFAULT_PROM_URL/DEFAULT_START feed both the argparse defaults and this
    # --help text; every main() test elsewhere passes --prom/--start explicitly,
    # so without this, mutating either constant would go undetected. Literal
    # strings (not check_series.DEFAULT_PROM_URL/DEFAULT_START) keep the check
    # from moving with the mutant.
    monkeypatch.setattr(sys, "argv", ["check_series.py", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        check_series.main()
    out, _ = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "default: http://localhost:9090" in out
    assert "default: 2026-09-26T03:30:00+02:00" in out
    # The module docstring feeds argparse's `description=__doc__`; without this,
    # description=None (or the arg dropped entirely) would go undetected.
    assert 'Detect the "two series, one legend" bug' in out
    # formatter_class=RawDescriptionHelpFormatter preserves the docstring's own
    # line breaks/indentation verbatim; argparse's default HelpFormatter would
    # instead reflow this whole paragraph into unindented, rewrapped lines.
    assert "\n    uv run tools/check_series.py <json_path> [--prom URL]" in out
    # "--prom" is a genuine alias (not just an abbreviation of "--prom-url",
    # which argparse would accept either way): only present if both strings are
    # actually registered, with "--prom" spelled/cased exactly.
    assert "--prom, --prom-url PROM" in out
    # Trailing "...)\n"/"...to\n" (each is the last thing on its help line) pins
    # the exact literal: a mutant that wraps the text (e.g. "XX...XX") still
    # contains the bare phrase as a substring, but not immediately followed by
    # the line break.
    assert "RFC3339, epoch, or 'now' (default: now)\n" in out
    assert "range-query step, e.g. 30s/2m (default: 30s)\n" in out
    assert "comma-separated panel ids to restrict to\n" in out


def test_main_block_via_runpy(monkeypatch, tmp_path, prometheus_stub, project_root):
    dashboard = {"panels": [{"id": 1, "title": "P", "type": "timeseries", "targets": [{"refId": "A", "expr": "m"}]}]}
    path = _write_dashboard(tmp_path, dashboard)
    prometheus_stub.responses[("query_range", "m")] = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(sys, "argv", _base_argv(path, prometheus_stub.url))
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(project_root / "tools" / "check_series.py"), run_name="__main__")
    assert exc_info.value.code == 0
