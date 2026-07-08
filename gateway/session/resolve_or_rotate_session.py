"""Session resolution for polled inbound Telegram messages."""

from __future__ import annotations

from core.agent_harness.session import SessionCore
from gateway.config.get_gateway_settings import TelegramInboundMessage
from gateway.polling.telegram_poller.client import TelegramBotClient
from gateway.session.inbound_message_security import (
    InboundDecision,
    persist_policy_if_needed,
)
from gateway.storage import SessionResolver


def resolve_or_rotate_session(
    event: TelegramInboundMessage,
    decision: InboundDecision,
    *,
    session_resolver: SessionResolver,
    client: TelegramBotClient,
) -> SessionCore | None:
    """Apply inbound decision side effects, then resolve or rotate the REPL session."""
    persist_policy_if_needed(decision)

    if decision.reply_text and decision.reply_text != "__ROTATE_SESSION__":
        client.send_message(event.chat_id, decision.reply_text)
        if not decision.allowed:
            return None

    if not decision.allowed and decision.reply_text != "__ROTATE_SESSION__":
        return None

    if decision.reply_text == "__ROTATE_SESSION__":
        session = session_resolver.rotate(user_id=event.user_id, chat_id=event.chat_id)
        client.send_message(event.chat_id, "Started a new session.")
        if event.text.strip().lower() == "/new":
            return None
        return session

    return session_resolver.resolve(user_id=event.user_id, chat_id=event.chat_id)
