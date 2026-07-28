"""Tests for gather context window truncation in evidence_driver (issue #4345)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from core.agent_harness.turns import evidence_driver
from core.state import MAX_CONVERSATION_MESSAGES


def _make_session(messages: list[dict[str, Any]]) -> MagicMock:
    s = MagicMock()
    s.cli_agent_messages = messages
    return s


def _fake_message(i: int) -> dict[str, Any]:
    return {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}


@contextmanager
def _capture_forwarded_messages() -> Iterator[list[list[Any]]]:
    """Record what reaches ``format_recent_conversation``, keeping its real output."""
    captured: list[list[Any]] = []
    real_fmt = evidence_driver.format_recent_conversation

    def _spy(msgs: list[Any], **kwargs: Any) -> str:
        captured.append(list(msgs))
        return real_fmt(msgs, **kwargs)

    with patch.object(evidence_driver, "format_recent_conversation", _spy):
        yield captured


def test_uses_all_messages_when_below_limit() -> None:
    """When history is short, all messages must be passed to format_recent_conversation."""
    # Arrange
    messages = [_fake_message(i) for i in range(5)]
    session = _make_session(messages)

    # Act
    with _capture_forwarded_messages() as captured:
        evidence_driver._build_gather_user_message(session, "question")

    # Assert
    assert captured, "format_recent_conversation was not called"
    assert len(captured[0]) == 5, "All 5 messages must be forwarded when below limit"


def test_truncates_to_max_conversation_messages() -> None:
    """Only the last MAX_CONVERSATION_MESSAGES messages must reach format_recent_conversation."""
    # Arrange
    n = MAX_CONVERSATION_MESSAGES + 10
    messages = [_fake_message(i) for i in range(n)]
    session = _make_session(messages)

    # Act
    with _capture_forwarded_messages() as captured:
        evidence_driver._build_gather_user_message(session, "question")

    # Assert: the newest window is kept, and it ends at the most recent message.
    assert captured, "format_recent_conversation was not called"
    assert len(captured[0]) == MAX_CONVERSATION_MESSAGES, (
        f"Expected {MAX_CONVERSATION_MESSAGES} messages, got {len(captured[0])} (issue #4345)"
    )
    assert captured[0][-1]["content"] == f"msg-{n - 1}"
