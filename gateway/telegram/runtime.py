"""Shared Telegram polling runtime resources and lifecycle helpers."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from gateway.storage import SessionBindingStore, SessionResolver, connect_gateway_db
from gateway.telegram.poller.client import TelegramBotClient
from gateway.telegram.settings import GatewaySettings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TelegramPollingRuntime:
    """Resources shared by the Telegram polling service."""

    client: TelegramBotClient
    db: sqlite3.Connection
    session_resolver: SessionResolver
    chat_locks: dict[str, asyncio.Lock]
    executor: ThreadPoolExecutor


InitializeTelegramPollingRuntime = Callable[[GatewaySettings], TelegramPollingRuntime]
ShutdownTelegramPollingRuntime = Callable[[TelegramPollingRuntime], None]


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
        chat_locks={},
        executor=ThreadPoolExecutor(
            max_workers=settings.max_concurrent_turns,
            thread_name_prefix="GatewayTurn",
        ),
    )


def shutdown_telegram_polling_runtime(runtime: TelegramPollingRuntime) -> None:
    """Release resources created by :func:`initialize_telegram_polling_runtime`."""
    try:
        runtime.executor.shutdown(wait=True, cancel_futures=False)
    except Exception:
        logger.debug("[telegram-gateway] executor shutdown failed", exc_info=True)
    try:
        runtime.db.close()
    except Exception:
        logger.debug("[telegram-gateway] database close failed", exc_info=True)
