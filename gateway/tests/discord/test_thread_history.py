"""Discord thread-history session seeding contracts."""

from __future__ import annotations

from core.agent_harness.session import SessionCore
from gateway.transports.discord.thread_history import seed_session_from_discord_thread


def test_discord_history_seeds_canonical_message_tuples() -> None:
    session = SessionCore()
    history = [("user", "question"), ("assistant", "answer")]

    assert seed_session_from_discord_thread(session, history=history) == 2
    assert session.cli_agent_messages == history


def test_discord_history_does_not_replace_existing_session() -> None:
    session = SessionCore()
    existing = [("assistant", "existing answer")]
    session.cli_agent_messages = existing

    assert seed_session_from_discord_thread(session, history=[("user", "new question")]) == 0
    assert session.cli_agent_messages == existing
