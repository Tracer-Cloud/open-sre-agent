"""Bring up the gateway web app, mirroring each chat transport's ``startup``.

Returns the running handle plus the status line to report, so the caller owns
the component-status map rather than having it written behind its back.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from core.domain.alerts.inbox import AlertInbox, set_current_inbox
from gateway.web.web_server import WebAppServerHandle, serve_webapp_in_thread

DEFAULT_WEB_PORT = 8000


@dataclass(frozen=True)
class WebStartup:
    """A started (or skipped) web app and the status line describing it."""

    server: WebAppServerHandle | None
    status: str


def start_web_server(*, logger: logging.Logger) -> WebStartup:
    """Serve the shared web app (health probes + alert intake) in a daemon thread.

    A web app that cannot bind is not fatal: the gateway still serves chat.
    """
    set_current_inbox(AlertInbox())
    port = int(os.environ.get("PORT", str(DEFAULT_WEB_PORT)))
    try:
        server = serve_webapp_in_thread(host="0.0.0.0", port=port)
    except RuntimeError as exc:
        logger.warning("web app disabled: %s", exc)
        return WebStartup(server=None, status=f"failed ({exc})")
    logger.info("web app serving on http://%s", server.bound_address)
    return WebStartup(
        server=server,
        status=f"serving http://{server.bound_address} (health, alerts)",
    )


__all__ = ["DEFAULT_WEB_PORT", "WebStartup", "start_web_server"]
