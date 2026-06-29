"""Main gateway controller: routes inbound events to agent turns."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from gateway.approvals.store import ApprovalStore
from gateway.approvals.telegram import TelegramApprovalService
from gateway.config import GatewaySettings, TelegramInboundMessage, load_gateway_settings
from gateway.db import connect_gateway_db
from gateway.platforms.telegram.client import TelegramBotClient
from gateway.security.authorize import evaluate_inbound, persist_policy_if_needed
from gateway.session.bindings import SessionBindingStore
from gateway.session.resolver import SessionResolver
from gateway.turn_executor import execute_gateway_turn

logger = logging.getLogger(__name__)


class GatewayRunner:
    """Process Telegram inbound updates and run agent turns."""

    def __init__(self, settings: GatewaySettings | None = None) -> None:
        self.settings = settings or load_gateway_settings()
        if not self.settings.bot_token:
            msg = "TELEGRAM_BOT_TOKEN is required for the Telegram gateway"
            raise ValueError(msg)
        self._client = TelegramBotClient(self.settings.bot_token)
        self._db = connect_gateway_db()
        self._bindings = SessionBindingStore(self._db)
        self._sessions = SessionResolver(self._bindings)
        self._approvals = ApprovalStore(self._db)
        self._approval_service = TelegramApprovalService(
            client=self._client,
            store=self._approvals,
            settings=self.settings,
        )
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._executor = ThreadPoolExecutor(max_workers=self.settings.max_concurrent_turns)
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_turns)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def handle_inbound(self, event: TelegramInboundMessage) -> None:
        if event.callback_query_id:
            await self._handle_callback(event)
            return
        await self._handle_message(event)

    async def _handle_callback(self, event: TelegramInboundMessage) -> None:
        self._approval_service.handle_callback(
            user_id=event.user_id,
            callback_data=event.callback_data,
            callback_query_id=event.callback_query_id,
        )

    async def _handle_message(self, event: TelegramInboundMessage) -> None:
        decision = evaluate_inbound(
            user_id=event.user_id,
            chat_id=event.chat_id,
            text=event.text,
            env_allowed_user_ids=self.settings.allowed_user_ids,
        )
        persist_policy_if_needed(decision)

        if decision.reply_text and decision.reply_text != "__ROTATE_SESSION__":
            self._client.send_message(event.chat_id, decision.reply_text)
            if not decision.allowed:
                return

        if not decision.allowed and decision.reply_text != "__ROTATE_SESSION__":
            return

        lock = self._chat_locks.setdefault(event.user_id, asyncio.Lock())
        async with lock, self._semaphore:
            if decision.reply_text == "__ROTATE_SESSION__":
                session = self._sessions.rotate(user_id=event.user_id)
                self._client.send_message(event.chat_id, "Started a new session.")
                if event.text.strip().lower() == "/new":
                    return
            else:
                session = self._sessions.resolve(user_id=event.user_id)

            loop = self._loop or asyncio.get_running_loop()
            await loop.run_in_executor(
                self._executor,
                lambda: execute_gateway_turn(
                    text=event.text,
                    session=session,
                    client=self._client,
                    chat_id=event.chat_id,
                    settings=self.settings,
                    approval_service=self._approval_service,
                ),
            )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._db.close()

    def setup_webhook(self) -> tuple[bool, str]:
        if not self.settings.webhook_url:
            return True, ""
        return self._client.set_webhook(self.settings.webhook_url, self.settings.webhook_secret)

    def clear_webhook(self) -> None:
        self._client.delete_webhook()


_runner: GatewayRunner | None = None
_runner_lock = threading.Lock()


def get_runner() -> GatewayRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = GatewayRunner()
        return _runner


def set_runner(runner: GatewayRunner | None) -> None:
    global _runner
    with _runner_lock:
        _runner = runner
