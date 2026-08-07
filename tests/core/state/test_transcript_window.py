"""Tests for transcript window compaction: overflow becomes a running summary."""

from __future__ import annotations

import pytest

from core.state.transcript_window import (
    SESSION_SUMMARY_PREFIX,
    compact_messages_to_window,
    format_messages_for_summary,
)

_MAX = 24


def _turns(n: int, start: int = 1) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i in range(start, start + n):
        out.append(("user", f"question {i}"))
        out.append(("assistant", f"answer {i}"))
    return out


def test_compact_is_noop_within_window() -> None:
    messages = _turns(12)

    assert compact_messages_to_window(messages, max_messages=_MAX) == messages


def test_overflow_compacts_into_leading_summary() -> None:
    messages = _turns(14)

    compacted = compact_messages_to_window(messages, max_messages=_MAX)

    assert len(compacted) <= _MAX
    role, content = compacted[0]
    assert role == "assistant"
    assert content.startswith(SESSION_SUMMARY_PREFIX)
    assert "question 1" in content
    assert compacted[-1] == ("assistant", "answer 14")
    assert compacted[1:] == messages[-(len(compacted) - 1) :]


def test_compact_keeps_whole_turns_after_summary() -> None:
    compacted = compact_messages_to_window(_turns(14), max_messages=_MAX)

    kept = compacted[1:]
    assert len(kept) % 2 == 0
    assert kept[0][0] == "user"


def test_recompaction_merges_into_single_summary() -> None:
    messages = _turns(14)
    compacted = compact_messages_to_window(messages, max_messages=_MAX)
    compacted.extend(_turns(3, start=15))

    recompacted = compact_messages_to_window(compacted, max_messages=_MAX)

    summaries = [m for m in recompacted if m[1].startswith(SESSION_SUMMARY_PREFIX)]
    assert len(summaries) == 1
    assert recompacted[0] == summaries[0]


def test_early_fact_survives_repeated_compactions() -> None:
    messages: list[tuple[str, str]] = [
        ("user", "my cluster is named prod-eu-42"),
        ("assistant", "Noted: prod-eu-42."),
    ]
    for i in range(2, 40):
        messages.append(("user", f"turn {i} question"))
        messages.append(("assistant", f"turn {i} answer"))
        messages = compact_messages_to_window(messages, max_messages=_MAX)

    assert len(messages) <= _MAX
    assert "prod-eu-42" in messages[0][1]


def test_summary_truncates_tail_not_head() -> None:
    messages: list[tuple[str, str]] = [("user", "first-fact-alpha"), ("assistant", "ok")]
    for i in range(2, 30):
        messages.append(("user", f"padding {i} " + "x" * 600))
        messages.append(("assistant", "y" * 600))
        messages = compact_messages_to_window(messages, max_messages=_MAX, summary_max_chars=2_000)

    summary = messages[0][1]
    assert len(summary) <= len(SESSION_SUMMARY_PREFIX) + 2_000
    assert "first-fact-alpha" in summary


def test_tiny_caps_never_exceed_the_window() -> None:
    messages = _turns(5)

    three = compact_messages_to_window(messages, max_messages=3)
    assert len(three) <= 3
    assert three[0][1].startswith(SESSION_SUMMARY_PREFIX)
    assert three[1:] == messages[-2:]

    one = compact_messages_to_window(messages, max_messages=1)
    assert len(one) == 1
    assert one[0][1].startswith(SESSION_SUMMARY_PREFIX)
    assert "question 1" in one[0][1]


def test_rejects_nonpositive_window() -> None:
    with pytest.raises(ValueError, match="max_messages"):
        compact_messages_to_window(_turns(2), max_messages=0)


def test_format_messages_for_summary_truncates_long_lines() -> None:
    text = format_messages_for_summary([("user", "z" * 1_000)])

    assert text.startswith("- user: ")
    assert len(text) <= len("- user: ") + 700
    assert text.endswith("...")
