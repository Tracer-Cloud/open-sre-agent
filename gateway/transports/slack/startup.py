"""Start the Slack chat worker for the gateway.

Owns what is particular to starting Slack: loading settings and bringing up the
configured inbound transport. Socket Mode holds one WebSocket; Events API HTTP
serves signed POSTs on its own port. The message handler is transport-agnostic
and injected by the composition root, so this module holds no dispatch logic.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping

from gateway.core.runtime.errors import GatewayConfigurationError
from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.transports.slack.processing.events import SlackInboundMessage
from gateway.transports.slack.settings import (
    SlackGatewaySettings,
    SlackInboundTransport,
    load_slack_gateway_settings,
)
from gateway.transports.slack.transport.events_api.receiver import (
    InMemorySlackEventDeduplicator,
    SlackEventDeduplicator,
)
from gateway.transports.slack.transport.events_api.server import (
    ListenerGate,
    SlackHttpServerHandle,
    build_slack_http_app,
    serve_slack_http_in_thread,
)
from gateway.transports.slack.transport.socket_mode.worker import (
    SlackGatewayBackground,
    start_slack_gateway_background,
)
from gateway.transports.slack.turn_stack import build_slack_turn_stack

SlackWorker = SlackGatewayBackground | SlackHttpServerHandle

#: Opt in to single-replica dedup when no shared store is configured.
LOCAL_DEDUP_ENV = "SLACK_GATEWAY_ALLOW_LOCAL_DEDUP"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def _start_socket_mode(
    *, settings: SlackGatewaySettings, logger: logging.Logger, handler: GatewayAgentCallback
) -> SlackWorker:
    return start_slack_gateway_background(settings=settings, logger=logger, handler=handler)


def _build_deduplicator(logger: logging.Logger) -> SlackEventDeduplicator:
    """Postgres when ``DATABASE_URL`` is set, else process-local.

    Slack delivers at least once, so a retry landing on another replica is
    admitted again unless the handled set is shared. The in-memory fallback is
    correct for exactly one replica — local development and single-instance
    deploys — and is warned about so it is never a silent choice.
    """
    dsn = os.getenv("DATABASE_URL", "").strip()
    if dsn:
        from gateway.transports.slack.persistence.event_dedup import (
            PostgresSlackEventDeduplicator,
        )

        return PostgresSlackEventDeduplicator(dsn)
    if not _env_flag(LOCAL_DEDUP_ENV):
        raise GatewayConfigurationError(
            "Slack Events API HTTP needs a shared event store: set DATABASE_URL. "
            "Without one each replica keeps its own handled-event set, so a Slack "
            f"retry runs the turn twice. Set {LOCAL_DEDUP_ENV}=1 to accept "
            "process-local dedup on a single replica."
        )
    logger.warning(
        "[slack-gateway] %s: event dedup is process-local — safe only while "
        "exactly one replica runs",
        LOCAL_DEDUP_ENV,
    )
    return InMemorySlackEventDeduplicator()


def _start_events_api_http(
    *, settings: SlackGatewaySettings, logger: logging.Logger, handler: GatewayAgentCallback
) -> SlackWorker:
    stack = build_slack_turn_stack(settings=settings, logger=logger, handler=handler)

    def _submit_turn(message: SlackInboundMessage) -> None:
        # The route must answer Slack within three seconds, so the turn runs
        # on the shared executor rather than inside the request.
        stack.executor.submit(stack.dispatcher.dispatch, message)

    gate = ListenerGate()
    app = build_slack_http_app(
        settings=settings,
        approvals=stack.approvals,
        deduplicator=_build_deduplicator(logger),
        submit_turn=_submit_turn,
        gate=gate,
    )
    handle = serve_slack_http_in_thread(
        app=app, port=settings.http_port, workers=stack.executor, gate=gate
    )
    logger.info("[slack-gateway] events api listening on %s", handle.bound_address)
    return handle


# One row per inbound transport — adding a transport adds a row, not a branch.
_TRANSPORT_STARTERS: Mapping[
    SlackInboundTransport,
    Callable[..., SlackWorker],
] = {
    SlackInboundTransport.SOCKET_MODE: _start_socket_mode,
    SlackInboundTransport.EVENTS_API_HTTP: _start_events_api_http,
}


def start_slack_worker(
    *,
    logger: logging.Logger,
    handler: GatewayAgentCallback,
) -> tuple[SlackWorker, SlackGatewaySettings]:
    """Load Slack settings and start the configured inbound transport.

    ``handler`` is the transport-agnostic per-message callback. Returns the
    running worker plus the resolved settings for the composition root to hold.
    Raises :class:`GatewayConfigurationError` when Slack is not configured —
    the composition root decides whether that is fatal.
    """
    settings = load_slack_gateway_settings()
    start = _TRANSPORT_STARTERS[settings.inbound_transport]
    worker = start(settings=settings, logger=logger, handler=handler)
    return worker, settings


__all__ = ["SlackWorker", "start_slack_worker"]
