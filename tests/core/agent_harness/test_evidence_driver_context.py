"""Tests for gather context window truncation in evidence_driver (issue #4345)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from core.agent_harness.turns.evidence_driver import _build_gather_user_message
from core.state import MAX_CONVERSATION_MESSAGES


def _make_session(messages: list[dict[str, Any]]) -> MagicMock:
    s = MagicMock()
    s.cli_agent_messages = messages
    return s


def _fake_message(i: int) -> dict[str, Any]:
    return {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}


def test_uses_all_messages_when_below_limit() -> None:
    """When history is short, all messages must be passed to format_recent_conversation."""
    messages = [_fake_message(i) for i in range(5)]
    session = _make_session(messages)

    captured: list[list[Any]] = []

    import core.agent_harness.turns.evidence_driver as mod

    real_fmt = mod.format_recent_conversation

    def _spy(msgs: list[Any], **kwargs: Any) -> str:
        captured.append(list(msgs))
        return real_fmt(msgs, **kwargs)

    import unittest.mock

    with unittest.mock.patch.object(mod, "format_recent_conversation", _spy):
        _build_gather_user_message(session, "question")

    assert captured, "format_recent_conversation was not called"
    assert len(captured[0]) == 5, "All 5 messages must be forwarded when below limit"


def test_truncates_to_max_conversation_messages() -> None:
    """Only the last MAX_CONVERSATION_MESSAGES messages must reach format_recent_conversation."""
    n = MAX_CONVERSATION_MESSAGES + 10
    messages = [_fake_message(i) for i in range(n)]
    session = _make_session(messages)

    captured: list[list[Any]] = []

    import core.agent_harness.turns.evidence_driver as mod

    real_fmt = mod.format_recent_conversation

    def _spy(msgs: list[Any], **kwargs: Any) -> str:
        captured.append(list(msgs))
        return real_fmt(msgs, **kwargs)

    import unittest.mock

    with unittest.mock.patch.object(mod, "format_recent_conversation", _spy):
        _build_gather_user_message(session, "question")

    assert captured, "format_recent_conversation was not called"
    assert len(captured[0]) == MAX_CONVERSATION_MESSAGES, (
        f"Expected {MAX_CONVERSATION_MESSAGES} messages, got {len(captured[0])} (issue #4345)"
    )
    assert captured[0][-1]["content"] == f"msg-{n - 1}"
