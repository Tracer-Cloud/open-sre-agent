from __future__ import annotations

import base64
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from tests.benchmarks.orcabench.config import GrafanaSettings
from tests.benchmarks.orcabench.execution.health import check_grafana


class GrafanaHealthHandler(BaseHTTPRequestHandler):
    """Small real HTTP service implementing the ORCA Grafana health contract."""

    seen_authorization: ClassVar[str | None] = None

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        type(self).seen_authorization = self.headers.get("Authorization")
        body = json.dumps(
            {"database": "ok", "version": "test-grafana"},
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_grafana_health_uses_real_http_and_basic_auth() -> None:
    GrafanaHealthHandler.seen_authorization = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), GrafanaHealthHandler)
    except PermissionError:
        pytest.skip("runtime sandbox does not permit binding a loopback test service")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        result = check_grafana(f"http://{host}:{port}", GrafanaSettings(), 2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    expected = base64.b64encode(b"admin:admin").decode()
    assert GrafanaHealthHandler.seen_authorization == f"Basic {expected}"
    assert result == {"status": "ready", "database": "ok", "version": "test-grafana"}
