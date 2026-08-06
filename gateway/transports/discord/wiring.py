"""Discord-specific gateway wiring."""

from __future__ import annotations

import logging

from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.transports.discord.background import (
    DiscordGatewayBackground,
    start_discord_gateway_background,
)
from gateway.transports.discord.settings import (
    DiscordGatewaySettings,
    load_discord_gateway_settings,
)


def start_discord_worker(
    *,
    logger: logging.Logger,
    handler: GatewayAgentCallback,
) -> tuple[DiscordGatewayBackground, DiscordGatewaySettings]:
    """Load Discord settings and start the Gateway WebSocket background worker."""
    settings = load_discord_gateway_settings()
    worker = start_discord_gateway_background(
        settings=settings,
        logger=logger,
        handler=handler,
    )
    return worker, settings


__all__ = ["start_discord_worker"]
