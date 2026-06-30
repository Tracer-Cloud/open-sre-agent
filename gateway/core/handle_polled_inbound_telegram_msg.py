"""Handlers for polled inbound Telegram messages."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from gateway.approvals.telegram import TelegramApprovalService
from gateway.config.get_gateway_settings import GatewaySettings, TelegramInboundMessage
from gateway.core.dispatch_gateway_msg_to_agent import dispatch_gateway_msg_to_agent
from gateway.core.enforce_inbound_telegram_message_security import (
    enforce_inbound_telegram_message_security,
)
from gateway.core.gateway_output_sink import GatewayOutputSink
from gateway.core.resolve_or_rotate_session import resolve_or_rotate_session
from gateway.core.telegram_poller.client import TelegramBotClient
from gateway.storage import SessionResolver

logger = logging.getLogger(__name__)


async def handle_polled_inbound_telegram_message(
    event: TelegramInboundMessage,
    *,
    client: TelegramBotClient,
    session_resolver: SessionResolver,
    approval_service: TelegramApprovalService,
    settings: GatewaySettings,
    executor: ThreadPoolExecutor,
    chat_locks: dict[str, asyncio.Lock],
    turn_semaphore: asyncio.Semaphore,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Process one long-polled inbound Telegram update (callback or message)."""
    if event.callback_query_id:
        approval_service.handle_callback(
            user_id=event.user_id,
            callback_data=event.callback_data,
            callback_query_id=event.callback_query_id,
        )
        return

    user_lock = chat_locks.setdefault(event.user_id, asyncio.Lock())
    decision = enforce_inbound_telegram_message_security(
        user_id=event.user_id,
        chat_id=event.chat_id,
        text=event.text,
        env_allowed_user_ids=settings.allowed_user_ids,
    )
    async with user_lock, turn_semaphore:
        session = resolve_or_rotate_session(
            event,
            decision,
            session_resolver=session_resolver,
            client=client,
        )
        if session is None:
            return

        sink = GatewayOutputSink(
            client=client,
            chat_id=event.chat_id,
            edit_interval_seconds=settings.stream_edit_interval_seconds,
        )

        event_loop = loop or asyncio.get_running_loop()
        await event_loop.run_in_executor(
            executor,
            lambda: dispatch_gateway_msg_to_agent(
                text=event.text,
                session=session,
                chat_id=event.chat_id,
                sink=sink,
                approval_service=approval_service,
                logger=logger,
            ),
        )
