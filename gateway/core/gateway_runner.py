"""Main gateway controller: routes inbound events to agent turns."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from gateway.approvals.store import ApprovalStore
from gateway.approvals.telegram import TelegramApprovalService
from gateway.config.get_gateway_settings import (
    GatewaySettings,
    TelegramInboundMessage,
    load_gateway_settings,
)
from gateway.core.handle_polled_inbound_telegram_msg import handle_polled_inbound_telegram_message
from gateway.platforms.telegram.client import TelegramBotClient
from gateway.storage import SessionBindingStore, SessionResolver, connect_gateway_db

logger = logging.getLogger(__name__)


class GatewayRunner:
    """Orchestrates inbound Telegram events and manages shared resources."""

    def __init__(self, settings: GatewaySettings | None = None) -> None:
        self.settings = settings or load_gateway_settings()
        if not self.settings.bot_token:
            msg = "TELEGRAM_BOT_TOKEN is required for the Telegram gateway"
            raise ValueError(msg)

        self._client = TelegramBotClient(self.settings.bot_token)
        self._db = connect_gateway_db()
        self._sessions = SessionResolver(SessionBindingStore(self._db))
        self._approval_service = TelegramApprovalService(
            client=self._client,
            store=ApprovalStore(self._db),
            settings=self.settings,
        )
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=self.settings.max_concurrent_turns,
            thread_name_prefix="GatewayTurn",
        )
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_turns)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def handle_inbound(self, event: TelegramInboundMessage) -> None:
        """Route inbound event to the appropriate handler."""
        if event.callback_query_id:
            self._approval_service.handle_callback(
                user_id=event.user_id,
                callback_data=event.callback_data,
                callback_query_id=event.callback_query_id,
            )
            return

        user_lock = self._chat_locks.setdefault(event.user_id, asyncio.Lock())
        await handle_polled_inbound_telegram_message(
            event,
            client=self._client,
            session_resolver=self._sessions,
            approval_service=self._approval_service,
            settings=self.settings,
            executor=self._executor,
            user_lock=user_lock,
            turn_semaphore=self._semaphore,
            loop=self._loop,
        )

    def shutdown(self) -> None:
        """Clean shutdown of runner resources."""
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._db.close()

    def clear_webhook(self) -> None:
        self._client.delete_webhook()
