"""Contracts for transport-neutral session history seeding."""

from __future__ import annotations

from core.agent_harness.session import SessionCore
from gateway.core.session import seed_session_history


def _session(messages: list[tuple[str, str]]) -> SessionCore:
    session = SessionCore()
    session.cli_agent_messages = messages
    return session


def test_seed_empty_session_with_canonical_message_tuples() -> None:
    session = _session([])
    history = [("user", "question"), ("assistant", "answer")]

    assert seed_session_history(session, history) == 2
    assert session.cli_agent_messages == history


def test_existing_session_is_not_replaced_by_default() -> None:
    existing = [("assistant", "existing answer")]
    session = _session(existing)

    assert seed_session_history(session, [("user", "new question")]) == 0
    assert session.cli_agent_messages == existing


def test_replace_existing_refreshes_history_and_filters_invalid_messages() -> None:
    session = _session([("assistant", "stale answer")])

    count = seed_session_history(
        session,
        [("user", "latest question"), ("tool", "ignored"), ("assistant", "  ")],
        replace_existing=True,
    )

    assert count == 1
    assert session.cli_agent_messages == [("user", "latest question")]
