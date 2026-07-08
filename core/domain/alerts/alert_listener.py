"""Local HTTP listener for pushed alert payloads."""

from __future__ import annotations

import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any
from urllib.parse import urlparse

from core.domain.alerts.inbox import AlertInbox, IncomingAlert

log = logging.getLogger(__name__)

# Cap on POST body size we'll accept from any caller (authed or not).
# Bounds the pre-auth body drain that prevents the macOS RST race, so
# a fake ``Content-Length: 1 GB`` can't stall the single-threaded
# handler thread before token validation runs.
#
# Sizing: realistic alert payloads (text + stack trace + log context)
# top out around 50 KB, so 1 MiB is ~20× headroom while keeping reads
# sub-millisecond on loopback and sub-second on typical networks. The
# cap bounds bytes, NOT duration: a slowloris client could still
# trickle 1 MiB byte-by-byte and tie up the handler for a long time.
# Add a socket read timeout if/when this surface is exposed beyond
# trusted callers.
_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB


def _wait_until_http_ready(host: str, port: int, *, timeout_s: float = 10.0) -> None:
    """Block until ``GET /healthz`` succeeds (TCP accept can happen before HTTP is usable)."""
    deadline = time.monotonic() + timeout_s
    last_err: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            conn = HTTPConnection(host, port, timeout=0.5)
            conn.request("GET", "/healthz")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            if resp.status == 200:
                return
        except (TimeoutError, OSError, ConnectionError) as exc:
            last_err = exc
            time.sleep(0.02)
    msg = f"alert listener on {host}:{port} did not become ready within {timeout_s}s"
    if last_err is not None:
        raise RuntimeError(msg) from last_err
    raise RuntimeError(msg)


@dataclass
class AlertListenerHandle:
    server: HTTPServer
    thread: Thread
    _inbox: AlertInbox
    _bound_host: str = ""
    _bound_port: int = 0

    def stop(self) -> None:
        if not getattr(self, "_stopped", False):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            self._stopped = True

    @property
    def bound_address(self) -> str:
        return f"{self._bound_host}:{self._bound_port}"

    @property
    def inbox(self) -> AlertInbox:
        return self._inbox


def start_alert_listener(
    inbox: AlertInbox,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
) -> AlertListenerHandle:
    if host != "127.0.0.1" and token is None:
        raise ValueError(
            "Refusing to bind alert listener to non-loopback address without a token. "
            "Set OPENSRE_ALERT_LISTENER_TOKEN or use 127.0.0.1."
        )

    def _make_handler(token: str | None) -> type[BaseHTTPRequestHandler]:
        class _AlertHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                log.debug(fmt, *args)

            def _check_auth(self) -> bool:
                if token is None:
                    return True
                auth = self.headers.get("Authorization", "")
                if hmac.compare_digest(auth, f"Bearer {token}"):
                    return True
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "unauthorized"}).encode())
                return False

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/healthz":
                    self._respond(200, {"status": "ok"})
                else:
                    self._respond(404, {"error": "not found"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != "/alerts":
                    self.send_response(404)
                    self.end_headers()
                    return
                # Read the body before the auth check (macOS RST race),
                # but validate Content-Length first — otherwise an
                # unauthenticated caller can stall this single-threaded
                # handler with a malformed or oversized header.
                try:
                    length = int(self.headers.get("Content-Length", 0))
                except ValueError:
                    self._respond(400, {"error": "invalid Content-Length"})
                    return
                if length < 0:
                    self._respond(400, {"error": "invalid Content-Length"})
                    return
                if length > _MAX_BODY_BYTES:
                    self._respond(413, {"error": "payload too large"})
                    return
                raw = self.rfile.read(length)
                if not self._check_auth():
                    return
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    self._respond(400, {"error": "invalid json"})
                    return
                try:
                    if "received_at" not in data or data.get("received_at") is None:
                        data["received_at"] = datetime.now(UTC)
                    alert = IncomingAlert.model_validate(data)
                except Exception as exc:
                    self._respond(400, {"error": str(exc)})
                    return

                accepted = inbox.put(alert)

                if not accepted:
                    self._respond(
                        202,
                        {
                            "queued": True,
                            "queue_depth": inbox.qsize,
                            "dropped": inbox.dropped,
                            "warning": "inbox full, oldest alert dropped",
                        },
                    )
                else:
                    self._respond(
                        202,
                        {
                            "queued": True,
                            "queue_depth": inbox.qsize,
                        },
                    )

            def _respond(self, code: int, body: dict[str, Any]) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

        return _AlertHandler

    handler_cls = _make_handler(token)
    server = HTTPServer((host, port), handler_cls)
    addr = server.server_address
    bound_host = str(addr[0])
    bound_port = int(addr[1])
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_http_ready(bound_host, bound_port)
    return AlertListenerHandle(
        server=server,
        thread=thread,
        _inbox=inbox,
        _bound_host=bound_host,
        _bound_port=bound_port,
    )


__all__ = [
    "AlertListenerHandle",
    "_MAX_BODY_BYTES",
    "start_alert_listener",
]
