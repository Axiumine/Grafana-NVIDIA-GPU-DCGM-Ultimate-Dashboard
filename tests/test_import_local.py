# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Tests for tools/import_local.py, exercised against a local stub Grafana server."""

import base64
import json
import runpy
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import import_local
import pytest


class _ImportHandler(BaseHTTPRequestHandler):
    """Records the request and replies with whatever `self.server.responder` says."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw.decode("utf-8")) if raw else None
        self.server.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})

        status, resp_body = self.server.responder(body)
        payload = json.dumps(resp_body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        """Silence BaseHTTPRequestHandler's default per-request stderr logging."""


@pytest.fixture
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ImportHandler)
    server.requests = []
    server.responder = lambda _body: (200, {"uid": "default-uid"})
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _base_url(server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


def test_main_imports_successfully_and_prints_url(stub_server, tmp_path, monkeypatch, capsys):
    stub_server.responder = lambda _body: (
        200,
        {"uid": "orig-dev", "slug": "myslug", "importedUrl": "/d/orig-dev/myslug"},
    )
    dashboard = {"uid": "orig", "id": 999, "panels": []}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))
    base_url = _base_url(stub_server)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_local.py",
            str(json_path),
            "--uid-suffix",
            "dev",
            "--grafana-url",
            base_url,
            "--datasource-uid",
            "ds-uid",
            "--user",
            "alice",
            "--password",
            "s3cret",
            "--folder-id",
            "7",
        ],
    )

    exit_code = import_local.main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert out == f"imported OK: uid=orig-dev slug=myslug\nURL: {base_url}/d/orig-dev/myslug\n"

    req = stub_server.requests[-1]
    assert req["path"] == "/api/dashboards/import"
    assert req["auth"] == "Basic " + base64.b64encode(b"alice:s3cret").decode()

    body = req["body"]
    assert body["overwrite"] is True
    assert body["folderId"] == 7
    assert body["inputs"] == [
        {"name": "DS_PROMETHEUS", "type": "datasource", "pluginId": "prometheus", "value": "ds-uid"}
    ]
    assert body["dashboard"]["uid"] == "orig-dev"
    assert body["dashboard"]["id"] is None  # never re-target a prior export's numeric id


def test_main_uid_suffix_falls_back_to_dashboard_default_uid(stub_server, tmp_path, monkeypatch, capsys):
    """dashboard.get("uid") is falsy (no "uid" key at all) -> base_uid falls back to "dashboard"."""
    stub_server.responder = lambda _body: (200, {"uid": "dashboard-x"})
    dashboard = {"panels": []}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))

    monkeypatch.setattr(
        sys,
        "argv",
        ["import_local.py", str(json_path), "--uid-suffix", "x", "--grafana-url", _base_url(stub_server)],
    )
    exit_code = import_local.main()
    capsys.readouterr()

    assert exit_code == 0
    assert stub_server.requests[-1]["body"]["dashboard"]["uid"] == "dashboard-x"


@pytest.mark.parametrize("extra_args", [[], ["--uid-suffix", ""]])
def test_main_without_a_real_uid_suffix_leaves_uid_untouched(stub_server, tmp_path, monkeypatch, capsys, extra_args):
    """--uid-suffix is falsy (default None, or an explicit empty string) -> uid is not rewritten."""
    stub_server.responder = lambda _body: (200, {"uid": "keep-me"})
    dashboard = {"uid": "keep-me", "panels": []}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))

    monkeypatch.setattr(
        sys, "argv", ["import_local.py", str(json_path), "--grafana-url", _base_url(stub_server), *extra_args]
    )
    exit_code = import_local.main()
    capsys.readouterr()

    assert exit_code == 0
    assert stub_server.requests[-1]["body"]["dashboard"]["uid"] == "keep-me"


def test_main_falls_back_to_importeduri_and_default_url_shape(stub_server, tmp_path, monkeypatch, capsys):
    """Success can be signalled by "importedUri" alone; missing "uid"/"importedUrl" fall
    back to the dashboard's own uid and the "/d/<uid>" URL shape."""
    stub_server.responder = lambda _body: (200, {"importedUri": "db/keep", "slug": "s"})
    dashboard = {"uid": "keep", "panels": []}
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps(dashboard))
    base_url = _base_url(stub_server)

    monkeypatch.setattr(sys, "argv", ["import_local.py", str(json_path), "--grafana-url", base_url])
    exit_code = import_local.main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert out == f"imported OK: uid=keep slug=s\nURL: {base_url}/d/keep\n"
    assert stub_server.requests[-1]["body"]["folderId"] == 0  # --folder-id default


@pytest.mark.parametrize("response_body", [{}, {"uid": None, "importedUri": False}])
def test_main_reports_error_when_success_flag_missing_or_false(
    stub_server, tmp_path, monkeypatch, capsys, response_body
):
    stub_server.responder = lambda _body: (200, response_body)
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps({"panels": []}))

    monkeypatch.setattr(sys, "argv", ["import_local.py", str(json_path), "--grafana-url", _base_url(stub_server)])
    exit_code = import_local.main()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "ERROR: import response missing uid/importedUri:" in captured.err


def test_main_reports_error_on_non_2xx_http_status(stub_server, tmp_path, monkeypatch, capsys):
    stub_server.responder = lambda _body: (500, {"message": "boom"})
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps({"panels": []}))

    monkeypatch.setattr(sys, "argv", ["import_local.py", str(json_path), "--grafana-url", _base_url(stub_server)])
    exit_code = import_local.main()
    err = capsys.readouterr().err

    assert exit_code == 1
    assert f"ERROR: HTTP 500 importing {json_path}:" in err
    assert '"message": "boom"' in err


def test_main_reports_error_when_grafana_is_unreachable(tmp_path, monkeypatch, capsys):
    # Bind then immediately close a socket to get a port nothing is listening on.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    grafana_url = f"http://127.0.0.1:{port}"

    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps({"panels": []}))

    monkeypatch.setattr(sys, "argv", ["import_local.py", str(json_path), "--grafana-url", grafana_url])
    exit_code = import_local.main()
    err = capsys.readouterr().err

    assert exit_code == 1
    assert f"ERROR: could not reach {grafana_url}/api/dashboards/import:" in err


def test_dunder_main_exits_zero_on_successful_import(stub_server, tmp_path, monkeypatch):
    stub_server.responder = lambda _body: (200, {"uid": "abc"})
    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps({"panels": []}))

    monkeypatch.setattr(sys, "argv", ["import_local.py", str(json_path), "--grafana-url", _base_url(stub_server)])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(import_local.__file__, run_name="__main__")
    assert exc_info.value.code == 0


def test_dunder_main_exits_nonzero_when_unreachable(tmp_path, monkeypatch):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    json_path = tmp_path / "dash.json"
    json_path.write_text(json.dumps({"panels": []}))

    monkeypatch.setattr(sys, "argv", ["import_local.py", str(json_path), "--grafana-url", f"http://127.0.0.1:{port}"])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(import_local.__file__, run_name="__main__")
    assert exc_info.value.code == 1
