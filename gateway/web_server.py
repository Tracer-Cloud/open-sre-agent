"""Serve the gateway web app in a background thread.

One shared FastAPI app (:mod:`gateway.webapp`), one port per host process: the
gateway daemon serves it on ``PORT`` and the interactive shell serves it on the
configured alert-listener address. ``port=0`` binds an ephemeral free port.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import uvicorn


@dataclass
class WebAppServerHandle:
    server: uvicorn.Server
    thread: threading.Thread
    bound_host: str
    bound_port: int

    @property
    def bound_address(self) -> str:
        return f"{self.bound_host}:{self.bound_port}"

    def stop(self, *, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=timeout)


def serve_webapp_in_thread(
    *, host: str = "127.0.0.1", port: int = 0, startup_timeout: float = 10.0
) -> WebAppServerHandle:
    """Start uvicorn serving :mod:`gateway.webapp` and wait until it is bound."""
    from gateway.webapp import app

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_config=None))
    thread = threading.Thread(target=server.run, name="gateway-web", daemon=True)
    thread.start()

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if server.started and server.servers:
            bound = server.servers[0].sockets[0].getsockname()
            return WebAppServerHandle(server, thread, str(bound[0]), int(bound[1]))
        if not thread.is_alive():
            break
        time.sleep(0.05)
    raise RuntimeError(f"web app on {host}:{port} failed to start")


__all__ = ["WebAppServerHandle", "serve_webapp_in_thread"]
