# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Giovanni Manzoni
"""Reusable local fake Prometheus HTTP API, shared by test_check_queries.py and test_check_series.py."""

import json
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def closed_port_url() -> str:
    """A http://127.0.0.1:<port>/ URL with nothing listening, for exercising the
    connection-refused / URLError path without monkeypatching urlopen."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def _lookup_key(path: str, params: dict) -> tuple | None:
    if path == "/api/v1/query":
        return ("query", params.get("query", ""))
    if path == "/api/v1/query_range":
        return ("query_range", params.get("query", ""))
    prefix, suffix = "/api/v1/label/", "/values"
    if path.startswith(prefix) and path.endswith(suffix):
        label = urllib.parse.unquote(path[len(prefix) : -len(suffix)])
        return ("label", label, params.get("match[]", ""))
    return None


def _render(entry: object) -> tuple[int, bytes]:
    if entry is None:
        return 404, b"stub: no response programmed for this request"
    if isinstance(entry, int):
        return entry, b""
    status, payload = entry if isinstance(entry, tuple) else (200, entry)
    if isinstance(payload, bytes):
        return status, payload
    if isinstance(payload, str):
        return status, payload.encode("utf-8")
    return status, json.dumps(payload).encode("utf-8")


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args) -> None:  # noqa: A002 (BaseHTTPRequestHandler signature)
        pass  # keep test output quiet

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        stub = self.server.stub
        stub.requests.append({"path": path, "params": params})

        key = _lookup_key(path, params)
        entry = stub.responses.get(key)
        if callable(entry):
            entry = entry(params)
        if entry is None and key is not None and key[0] == "label":
            # An unprogrammed label lookup defaults to a clean "no data" 200 rather
            # than a 404: main()'s $job/$instance/$gpu lookups hit this endpoint on
            # every run, and most tests don't care about their values.
            entry = {"status": "success", "data": []}

        status, body = _render(entry)
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _StubServer(ThreadingHTTPServer):
    daemon_threads = True  # don't let a per-connection thread outlive the test


class FakePrometheus:
    """Local ThreadingHTTPServer standing in for Prometheus' HTTP API.

    `responses` is keyed by:
      - ("query", "<promql expr>")               -- GET /api/v1/query
      - ("query_range", "<promql expr>")         -- GET /api/v1/query_range
      - ("label", "<label name>", "<match[]>")   -- GET /api/v1/label/<name>/values
        (match[] is "" when the request sent none, as fetch_a_real_gpu_uuid() does)

    Each value is a dict (JSON body, status 200), a str/bytes (raw body, status
    200), an int (that status, empty body), a (status, dict|str|bytes) tuple, or a
    callable receiving the decoded query-param dict and returning any of those. A
    request to /api/v1/query or /api/v1/query_range whose key has no programmed
    response gets a plain-text 404 (loud and easy to notice in a test that forgot
    to program it); an unprogrammed label-values lookup instead gets a clean
    200/no-data response, since main()'s $job/$instance/$gpu lookups fire on every
    run regardless of whether a test cares about their values (see do_GET below).

    Every request received is recorded in `.requests` as {"path": ..., "params": ...}.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: dict = {}
        self._server: _StubServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> FakePrometheus:
        self._server = _StubServer(("127.0.0.1", 0), _StubHandler)
        self._server.stub = self
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> FakePrometheus:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()
