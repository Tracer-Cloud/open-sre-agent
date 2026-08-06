"""Discord Gateway WebSocket transport for two-way agent chat.

Transport entry: :mod:`gateway.transports.discord.wiring` (``start_discord_worker``).
"""

from gateway.transports.discord.wiring import start_discord_worker

__all__ = ["start_discord_worker"]
