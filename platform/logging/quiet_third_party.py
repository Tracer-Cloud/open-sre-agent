"""Demote chatty third-party loggers so they do not paint the user TTY.

MCP's client session logs a WARNING when a tool returns without a cached
output schema (``Tool X not listed by server, cannot validate…``). That is
internal validation noise — keep it off the user-facing transcript.
"""

from __future__ import annotations

import logging

# Keep WARNING+ for transport libraries; MCP schema-cache misses are ERROR-only
# on the user-facing surfaces (shell + gateway).
_WARNING_FLOOR = (
    "httpx",
    "httpcore",
    "openai",
    "anthropic",
    "httpcore.connection",
    "httpcore.http11",
)

_ERROR_FLOOR = (
    "mcp",
    "mcp.client",
    "mcp.client.session",
)


def quiet_noisy_third_party_loggers() -> None:
    """Raise levels on known-noisy third-party loggers (idempotent)."""
    for name in _WARNING_FLOOR:
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in _ERROR_FLOOR:
        logging.getLogger(name).setLevel(logging.ERROR)


__all__ = ["quiet_noisy_third_party_loggers"]
