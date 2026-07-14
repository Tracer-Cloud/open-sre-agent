"""Live round-trip test for the shared in-thread web server."""

from __future__ import annotations

import json
import urllib.request

from core.domain.alerts.inbox import AlertInbox, set_current_inbox
from gateway.http.web_server import serve_webapp_in_thread


def test_serve_stop_round_trip_on_ephemeral_port() -> None:
    inbox = AlertInbox()
    set_current_inbox(inbox)
    handle = serve_webapp_in_thread(host="127.0.0.1", port=0)
    try:
        assert handle.bound_port > 0
        base = f"http://{handle.bound_address}"

        with urllib.request.urlopen(f"{base}/healthz", timeout=5) as resp:
            assert resp.status == 200

        request = urllib.request.Request(
            f"{base}/alerts",
            data=json.dumps({"text": "disk full"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            assert resp.status == 202

        queued = inbox.pop_nowait()
        assert queued is not None
        assert queued.text == "disk full"
    finally:
        handle.stop()
        set_current_inbox(None)

    assert not handle.thread.is_alive()
