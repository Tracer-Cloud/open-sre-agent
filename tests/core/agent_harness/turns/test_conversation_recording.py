"""Unit tests for record_conversation_turn.

Covers append, window shrink when over the message cap, and in-place list
update via ``[:]`` — without live API, network, or e2e harnesses.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_harness.turns.conversation_recording import record_conversation_turn
from core.state import MAX_CONVERSATION_MESSAGES
from core.state.transcript_window import SESSION_SUMMARY_PREFIX


def _session(messages: list[tuple[str, str]] | None = None) -> SimpleNamespace:
    return SimpleNamespace(cli_agent_messages=list(messages or []))


def _turns(n: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i in range(1, n + 1):
        out.append(("user", f"question {i}"))
        out.append(("assistant", f"answer {i}"))
    return out


def test_append_turn_adds_user_and_assistant() -> None:
    session = _session()

    record_conversation_turn(session, "hello", "hi there")

    assert session.cli_agent_messages == [
        ("user", "hello"),
        ("assistant", "hi there"),
    ]


def test_append_turn_extends_existing_history() -> None:
    session = _session([("user", "prior"), ("assistant", "reply")])

    record_conversation_turn(session, "follow-up", "noted")

    assert session.cli_agent_messages == [
        ("user", "prior"),
        ("assistant", "reply"),
        ("user", "follow-up"),
        ("assistant", "noted"),
    ]


def test_no_shrink_when_within_window() -> None:
    # MAX_CONVERSATION_MESSAGES is even (turns * 2); leave room for one more turn.
    turns_within = (MAX_CONVERSATION_MESSAGES // 2) - 1
    session = _session(_turns(turns_within))
    before_len = len(session.cli_agent_messages)

    record_conversation_turn(session, "last question", "last answer")

    assert len(session.cli_agent_messages) == before_len + 2
    assert len(session.cli_agent_messages) <= MAX_CONVERSATION_MESSAGES
    assert session.cli_agent_messages[-2:] == [
        ("user", "last question"),
        ("assistant", "last answer"),
    ]
    assert not any(
        content.startswith(SESSION_SUMMARY_PREFIX) for _, content in session.cli_agent_messages
    )


def test_window_shrinks_when_too_long() -> None:
    # Fill the window exactly, then one more turn overflows and must compact.
    session = _session(_turns(MAX_CONVERSATION_MESSAGES // 2))
    assert len(session.cli_agent_messages) == MAX_CONVERSATION_MESSAGES

    record_conversation_turn(session, "overflow question", "overflow answer")

    assert len(session.cli_agent_messages) <= MAX_CONVERSATION_MESSAGES
    role, content = session.cli_agent_messages[0]
    assert role == "assistant"
    assert content.startswith(SESSION_SUMMARY_PREFIX)
    assert session.cli_agent_messages[-2:] == [
        ("user", "overflow question"),
        ("assistant", "overflow answer"),
    ]


def test_update_is_in_place_same_list_object() -> None:
    """Compaction must assign via ``[:]`` so external aliases keep seeing updates."""
    session = _session(_turns(MAX_CONVERSATION_MESSAGES // 2))
    messages = session.cli_agent_messages
    list_id = id(messages)

    record_conversation_turn(session, "overflow question", "overflow answer")

    assert session.cli_agent_messages is messages
    assert id(session.cli_agent_messages) == list_id
    assert len(messages) <= MAX_CONVERSATION_MESSAGES
    assert messages[0][1].startswith(SESSION_SUMMARY_PREFIX)
    assert messages[-1] == ("assistant", "overflow answer")
