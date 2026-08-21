"""Serve the gateway web app in a background thread.

The threaded-server mechanism lives in :mod:`infrastructure.asgi_server`; this module
binds it to the gateway's own app (:mod:`gateway.web.webapp`). The daemon serves
it on ``PORT``; ``port=0`` binds an ephemeral free port.
"""

from __future__ import annotations

from infrastructure.asgi_server import AsgiServerHandle, serve_asgi_in_thread

#: The gateway web app's handle type — an alias so existing callers keep their name.
WebAppServerHandle = AsgiServerHandle


def serve_webapp_in_thread(
    *, host: str = "127.0.0.1", port: int = 0, startup_timeout: float = 10.0
) -> AsgiServerHandle:
    """Serve :mod:`gateway.web.webapp` in a thread and wait until it is bound."""
    from gateway.web.webapp import app

    return serve_asgi_in_thread(app, host=host, port=port, startup_timeout=startup_timeout)


__all__ = ["WebAppServerHandle", "serve_webapp_in_thread"]
