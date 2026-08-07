"""Session resolution for polled inbound Buzz messages."""

from __future__ import annotations

from core.agent_harness.session import SessionCore
from gateway.core.storage import SessionResolver
from gateway.transports.buzz.inbound_security import InboundDecision, persist_policy_if_needed
from gateway.transports.buzz.settings import BuzzInboundMessage
from integrations.buzz.client import BuzzClient


def conversation_key(event: BuzzInboundMessage) -> str:
    """Session-binding key: one session per ``(channel, sender)`` pair.

    Buzz channels have many members (unlike Telegram's 1:1 DMs), so the
    sender's pubkey alone cannot isolate sessions — two different people
    mentioning the bot in the same channel must not share one.
    """
    return f"{event.channel_id}:{event.pubkey}"


def resolve_or_rotate_session(
    event: BuzzInboundMessage,
    decision: InboundDecision,
    *,
    session_resolver: SessionResolver,
    client: BuzzClient,
) -> SessionCore | None:
    """Apply inbound decision side effects, then resolve or rotate the REPL session."""
    persist_policy_if_needed(decision)

    if decision.reply_text and decision.reply_text != "__ROTATE_SESSION__":
        client.send_message(channel=event.channel_id, content=decision.reply_text)
        if not decision.allowed:
            return None

    if not decision.allowed and decision.reply_text != "__ROTATE_SESSION__":
        return None

    key = conversation_key(event)
    if decision.reply_text == "__ROTATE_SESSION__":
        session = session_resolver.rotate(user_id=key, chat_id=event.channel_id)
        client.send_message(channel=event.channel_id, content="Started a new session.")
        if event.content.strip().lower() == "/new":
            return None
        return session

    return session_resolver.resolve(user_id=key, chat_id=event.channel_id)


__all__ = ["conversation_key", "resolve_or_rotate_session"]
