"""Handlers for polled inbound Telegram messages."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from core.agent_harness.session import SessionCore
from gateway.config.get_gateway_settings import GatewaySettings, TelegramInboundMessage
from gateway.gateway_output_sink import GatewayOutputSink
from gateway.polling.telegram_poller.client import TelegramBotClient
from gateway.session.inbound_message_security import (
    enforce_inbound_telegram_message_security,
)
from gateway.session.resolve_or_rotate_session import resolve_or_rotate_session
from gateway.storage import SessionResolver

logger = logging.getLogger(__name__)
GatewayAgentCallback = Callable[[str, SessionCore, GatewayOutputSink, logging.Logger], None]


async def handle_polled_inbound_telegram_message(
    event: TelegramInboundMessage,
    *,
    client: TelegramBotClient,
    session_resolver: SessionResolver,
    settings: GatewaySettings,
    executor: ThreadPoolExecutor,
    chat_locks: dict[str, asyncio.Lock],
    turn_semaphore: asyncio.Semaphore,
    loop: asyncio.AbstractEventLoop | None = None,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
) -> None:
    """Process one long-polled inbound Telegram update."""
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

        preview = event.text.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = f"{preview[:77]}..."
        logger.info(
            "inbound user=%s chat=%s session=%s text=%r",
            event.user_id,
            event.chat_id,
            session.session_id[:8],
            preview,
        )

        sink = GatewayOutputSink(
            client=client,
            chat_id=event.chat_id,
            edit_interval_seconds=settings.stream_edit_interval_seconds,
        )

        event_loop = loop or asyncio.get_running_loop()
        await event_loop.run_in_executor(
            executor,
            lambda: handle_callback_to_gateway_agent(
                event.text,
                session,
                sink,
                logger,
            ),
        )
