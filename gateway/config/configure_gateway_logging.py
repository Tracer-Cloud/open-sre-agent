"""Logging configuration for the Telegram gateway process."""

from __future__ import annotations

import logging

# Third-party libraries that become very noisy at INFO during normal gateway
# operation (long-poll getUpdates, Telegram send/edit, OpenAI completions).
_QUIET_LOGGER_NAMES = (
    "httpx",
    "httpcore",
    "openai",
)

# Routine authorized inbound audits are still emitted at INFO for other surfaces
# (Hermes, ops tooling) but are hidden in the dedicated gateway process.
_ROUTINE_AUDIT_MARKER = "authorized=True"


class _GatewayLogFormatter(logging.Formatter):
    """Present gateway package logs under a single short logger name."""

    def format(self, record: logging.LogRecord) -> str:
        if record.name == "gateway" or record.name.startswith("gateway."):
            record.name = "gateway"
        return super().format(record)


class _GatewayProcessLogFilter(logging.Filter):
    """Drop high-volume success-path noise from the gateway terminal."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "integrations.messaging_security" and record.levelno <= logging.INFO:
            message = record.getMessage()
            if _ROUTINE_AUDIT_MARKER in message:
                return False
        return True


def _quiet_noisy_loggers() -> None:
    for name in _QUIET_LOGGER_NAMES:
        logging.getLogger(name).setLevel(logging.WARNING)


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

    if not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            _GatewayLogFormatter(
                fmt="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        handler.addFilter(_GatewayProcessLogFilter())
        logging.basicConfig(level=logging.INFO, handlers=[handler])

    _quiet_noisy_loggers()
    return gateway_logger
