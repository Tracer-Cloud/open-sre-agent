"""Errors shared across gateway transports and the composition root."""

from __future__ import annotations


class GatewayConfigurationError(RuntimeError):
    """Raised when a gateway transport's configuration is missing or invalid."""


__all__ = ["GatewayConfigurationError"]
