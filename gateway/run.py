"""Gateway process entrypoint."""

from __future__ import annotations

import logging
import signal
import sys
import threading

from dotenv import load_dotenv

from gateway.background import run_poll_loop
from gateway.config import GatewaySettings, load_gateway_settings

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def start_gateway(*, poll: bool = False) -> None:
    """Start the Telegram gateway in webhook (uvicorn) or poll mode."""
    load_dotenv(override=False)
    _configure_logging()
    settings = load_gateway_settings()

    if poll or not settings.webhook_url:
        _run_poll_mode(settings)
        return

    import uvicorn

    uvicorn.run(
        "gateway.app:app",
        host=settings.host,
        port=settings.webhook_port,
        log_level="info",
    )


def _run_poll_mode(settings: GatewaySettings) -> None:
    stop_event = threading.Event()

    def _stop(*_args: object) -> None:
        logger.info("[telegram-gateway] shutting down")
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    logger.info("[telegram-gateway] poll mode started")
    run_poll_loop(settings, stop_event)


def main() -> None:
    poll = "--poll" in sys.argv
    start_gateway(poll=poll)


if __name__ == "__main__":
    main()
