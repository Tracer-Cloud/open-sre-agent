"""Slack-specific gateway wiring.

Owns everything that is particular to the Slack Socket Mode transport: loading
Slack settings and starting the background Socket Mode worker. The message
handler it drives is transport-agnostic and injected by the composition root,
so this module holds no agent/dispatch logic.
"""

from __future__ import annotations

import logging

from gateway.runtime.sink_protocol import GatewayAgentCallback
from gateway.slack.settings import SlackGatewaySettings, load_slack_gateway_settings
from gateway.slack.socket_mode_worker import (
    SlackGatewayBackground,
    start_slack_gateway_background,
)


def start_slack_worker(
    *,
    logger: logging.Logger,
    handler: GatewayAgentCallback,
) -> tuple[SlackGatewayBackground, SlackGatewaySettings]:
    """Load Slack settings and start the Socket Mode background worker.

    ``handler`` is the transport-agnostic per-message callback. Returns the
    running worker plus the resolved settings for the composition root to hold.
    Raises :class:`GatewayConfigurationError` when Slack is not configured —
    the composition root decides whether that is fatal.
    """
    settings = load_slack_gateway_settings()
    worker = start_slack_gateway_background(
        settings=settings,
        logger=logger,
        handler=handler,
    )
    return worker, settings


__all__ = ["start_slack_worker"]
