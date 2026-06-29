"""Logging configuration for the Telegram gateway process."""

from __future__ import annotations

import logging


def configure_gateway_logging() -> logging.Logger:
    """Configure root logging for the gateway process and return its logger."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger("gateway")
