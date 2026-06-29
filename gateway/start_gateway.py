"""Gateway process entrypoint."""

from __future__ import annotations

import signal
import sys
import threading

from dotenv import load_dotenv

from gateway.config.configure_gateway_logging import configure_gateway_logging
from gateway.config.get_gateway_settings import GatewaySettings, load_gateway_settings
from gateway.core.telegram_gateway_background import run_telegram_gateway_until_stopped


def open_gateway_ports(settings: GatewaySettings) -> None:
    # Open Up Gateway Ports for the Webhook Via FastAPI
    import uvicorn

    uvicorn.run(
        "gateway.app:app",
        host=settings.host,
        port=settings.webhook_port,
        log_level="info",
    )


def start_gateway(*, poll: bool = False) -> None:
    """Start the Telegram gateway in webhook (uvicorn) or poll mode."""
    load_dotenv(override=False)
    logger = configure_gateway_logging()
    settings = load_gateway_settings()

    # Do be checked. Do we really need then both settings such as FastAPI and the Poll Mode?
    # Or should we just do both by default?
    if poll or not settings.webhook_url:
        stop_event = threading.Event()

        def _stop(*_args: object) -> None:
            logger.info("[telegram-gateway] shutting down")
            stop_event.set()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        logger.info("[telegram-gateway] poll mode started")
        run_telegram_gateway_until_stopped(settings, stop_event)

    # Open Up Gateway Ports for the Webhook Via FastAPI, If Poll Mode is not enabled OR if the webhook URL is not set
    open_gateway_ports(settings)


def main() -> None:
    poll = "--poll" in sys.argv
    start_gateway(poll=poll)


if __name__ == "__main__":
    main()
