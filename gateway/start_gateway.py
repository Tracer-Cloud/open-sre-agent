"""Gateway process entrypoint."""

from __future__ import annotations

import signal
import sys

from dotenv import load_dotenv

from gateway.config.configure_gateway_logging import configure_gateway_logging
from gateway.config.get_gateway_settings import GatewayConfigurationError, load_gateway_settings
from gateway.polling.telegram_gateway_background import start_telegram_gateway_background
from gateway.polling.telegram_polling_runtime import (
    initialize_telegram_polling_runtime,
    shutdown_telegram_polling_runtime,
)


def start_gateway() -> None:
    """Start the Telegram gateway in long-poll mode."""
    load_dotenv(override=False)
    logger = configure_gateway_logging(co_located=False)

    try:
        settings = load_gateway_settings()
    except GatewayConfigurationError as exc:
        print(
            f"[telegram-gateway] could not start long-poll mode: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    handle = start_telegram_gateway_background(
        settings=settings,
        logger=logger,
        initialize_runtime=initialize_telegram_polling_runtime,
        shutdown_runtime=shutdown_telegram_polling_runtime,
    )

    def _stop(*_args: object) -> None:
        handle.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    handle.wait()


def main() -> None:
    start_gateway()


if __name__ == "__main__":
    main()
