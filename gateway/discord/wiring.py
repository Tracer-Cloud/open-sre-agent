"""Discord-specific gateway wiring."""

from __future__ import annotations

import logging

from gateway.discord.background import (
    DiscordGatewayBackground,
    start_discord_gateway_background,
)
from gateway.discord.settings import DiscordGatewaySettings, load_discord_gateway_settings
from gateway.runtime.sink_protocol import GatewayAgentCallback


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
