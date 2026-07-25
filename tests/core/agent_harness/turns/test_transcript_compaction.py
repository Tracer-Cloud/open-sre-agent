"""Char-threshold compaction must preserve an existing window summary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.agent_harness.turns.transcript_compaction import compact_session_branch
from core.state.transcript_window import SESSION_SUMMARY_PREFIX


class _FakeStorage:
    def __init__(self) -> None:
        self.compactions: list[dict[str, Any]] = []

    def append_compaction(self, session_id: str, **kwargs: Any) -> str:
        self.compactions.append({"session_id": session_id, **kwargs})
        return "entry-id"


class _FakeSession:
    def __init__(self, messages: list[tuple[str, str]]) -> None:
        self.agent = SimpleNamespace(messages=list(messages))
        self.storage = _FakeStorage()
        self.session_id = "session-under-test"


def _long_turns(n: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i in range(n):
        out.append(("user", f"question {i} " + "y" * 200))
        out.append(("assistant", f"answer {i} " + "z" * 200))
    return out


def test_char_compaction_preserves_existing_window_summary() -> None:
    """A fact sitting past the 700-char excerpt cut inside a prior window
    summary must survive char-threshold compaction."""
    deep_fact = "deep-fact-prod-eu-42"
    prior_summary = "x" * 750 + " " + deep_fact
    messages = [
        ("assistant", f"{SESSION_SUMMARY_PREFIX}{prior_summary}"),
        *_long_turns(10),
    ]
    session = _FakeSession(messages)

    result = compact_session_branch(session)

    assert result is not None
    head_role, head_content = session.agent.messages[0]
    assert head_role == "assistant"
    assert head_content.startswith(SESSION_SUMMARY_PREFIX)
    assert deep_fact in head_content
    summaries = [m for m in session.agent.messages if m[1].startswith(SESSION_SUMMARY_PREFIX)]
    assert len(summaries) == 1


def test_char_compaction_without_prior_summary_unchanged() -> None:
    session = _FakeSession(_long_turns(10))

    result = compact_session_branch(session)

    assert result is not None
    head = session.agent.messages[0][1]
    assert head.startswith(SESSION_SUMMARY_PREFIX)
    assert "Compacted" in head
    assert len(session.agent.messages) == 9
