"""Logging configuration for the Telegram gateway process."""

from __future__ import annotations

import logging


def configure_gateway_logging(*, co_located: bool = False) -> logging.Logger:
    """Configure the shared ``gateway`` logger for this process.

    Dedicated gateway processes configure root logging and emit INFO lines to
    the terminal. Co-located REPL runs attach a ``NullHandler`` so gateway
    diagnostics stay off the interactive shell output.
    """
    gateway_logger = logging.getLogger("gateway")
    if co_located:
        if not gateway_logger.handlers:
            gateway_logger.addHandler(logging.NullHandler())
            gateway_logger.propagate = False
        return gateway_logger

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return gateway_logger
