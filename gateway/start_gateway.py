"""Gateway process entrypoint."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from dotenv import load_dotenv

from gateway.approvals.store import ApprovalStore
from gateway.approvals.telegram import TelegramApprovalService
from gateway.config.configure_gateway_logging import configure_gateway_logging
from gateway.config.get_gateway_settings import (
    GatewayConfigurationError,
    GatewaySettings,
    load_gateway_settings,
)
from gateway.core.telegram_gateway_background import start_telegram_gateway_background
from gateway.core.telegram_poller.client import TelegramBotClient
from gateway.storage import SessionBindingStore, SessionResolver, connect_gateway_db


@dataclass(slots=True)
class TelegramPollingRuntime:
    """Resources shared by the Telegram polling service."""

    client: TelegramBotClient
    db: sqlite3.Connection
    session_resolver: SessionResolver
    approval_service: TelegramApprovalService
    chat_locks: dict[str, asyncio.Lock]
    executor: ThreadPoolExecutor


def initialize_telegram_polling_runtime(settings: GatewaySettings) -> TelegramPollingRuntime:
    """Wire shared Telegram gateway resources once."""
    if not settings.bot_token:
        msg = "TELEGRAM_BOT_TOKEN is required for the Telegram gateway"
        raise ValueError(msg)

    client = TelegramBotClient(settings.bot_token)
    db = connect_gateway_db()
    return TelegramPollingRuntime(
        client=client,
        db=db,
        session_resolver=SessionResolver(SessionBindingStore(db)),
        approval_service=TelegramApprovalService(
            client=client,
            store=ApprovalStore(db),
            settings=settings,
        ),
        chat_locks={},
        executor=ThreadPoolExecutor(
            max_workers=settings.max_concurrent_turns,
            thread_name_prefix="GatewayTurn",
        ),
    )


def shutdown_telegram_polling_runtime(runtime: TelegramPollingRuntime) -> None:
    """Release resources created by :func:`initialize_telegram_polling_runtime`."""
    with contextlib.suppress(Exception):
        runtime.executor.shutdown(wait=True, cancel_futures=False)
    with contextlib.suppress(Exception):
        runtime.db.close()


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
