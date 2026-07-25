"""Runtime/session compaction helpers for long REPL conversations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from core.state.transcript_window import (
    SESSION_SUMMARY_PREFIX,
    SUMMARY_MAX_CHARS,
    format_messages_for_summary,
    is_summary_message,
    merge_summary_texts,
    summary_text,
)

DEFAULT_AUTO_COMPACTION_CHARS = 48_000
_KEEP_RECENT_MESSAGES = 8


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    before_chars: int
    after_chars: int
    first_kept_entry_id: str


def _message_chars(messages: list[tuple[str, str]]) -> int:
    return sum(len(role) + len(text) + 2 for role, text in messages)


def should_compact(
    session: Any,
    *,
    threshold_chars: int | None = None,
) -> bool:
    # Headless / in-memory sessions do not have a persisted ``session.agent``;
    # they can never grow past a threshold worth compacting, so treat missing
    # attributes as "no compaction needed" rather than raising.
    agent = getattr(session, "agent", None)
    messages = getattr(agent, "messages", None) if agent is not None else None
    if messages is None:
        return False
    threshold = threshold_chars or _auto_threshold()
    return _message_chars(list(messages)) > threshold


def compact_session_branch(
    session: Any,
    *,
    summary: str | None = None,
    first_kept_entry_id: str = "",
) -> CompactionResult | None:
    """Compact the live session branch and persist a compaction entry.

    The LLM-summary path is intentionally optional at this layer. When callers
    do not provide a summary, compaction uses a deterministic fallback so the
    shell can always recover space without depending on another provider call.
    A leading session-summary message (from message-window compaction) is
    carried through whole and merged head-first, never excerpted per line.
    """

    messages = list(session.agent.messages)
    if len(messages) <= _KEEP_RECENT_MESSAGES:
        return None

    before_chars = _message_chars(messages)
    kept = messages[-_KEEP_RECENT_MESSAGES:]
    compacted = messages[:-_KEEP_RECENT_MESSAGES]
    prior = ""
    if compacted and is_summary_message(compacted[0]):
        prior = summary_text(compacted[0])
        compacted = compacted[1:]
    final_summary = merge_summary_texts(prior, summary or deterministic_summary(compacted))
    session.agent.messages = [("assistant", f"{SESSION_SUMMARY_PREFIX}{final_summary}"), *kept]
    after_chars = _message_chars(list(session.agent.messages))
    session.storage.append_compaction(
        session.session_id,
        summary=final_summary,
        first_kept_entry_id=first_kept_entry_id,
        before_chars=before_chars,
        after_chars=after_chars,
        before_tokens=_estimate_tokens(before_chars),
        after_tokens=_estimate_tokens(after_chars),
    )
    return CompactionResult(
        summary=final_summary,
        before_chars=before_chars,
        after_chars=after_chars,
        first_kept_entry_id=first_kept_entry_id,
    )


def auto_compact_if_needed(
    session: Any,
    *,
    threshold_chars: int | None = None,
) -> CompactionResult | None:
    if not should_compact(session, threshold_chars=threshold_chars):
        return None
    return compact_session_branch(session)


def deterministic_summary(messages: list[tuple[str, str]]) -> str:
    if not messages:
        return ""
    first = format_messages_for_summary(messages[:4])
    recent = format_messages_for_summary(messages[-4:]) if len(messages) > 4 else ""
    parts = [
        f"Compacted {len(messages)} earlier conversation messages.",
        "Earlier context:",
        first,
    ]
    if recent:
        parts.extend(["Most recent compacted context:", recent])
    return "\n".join(part for part in parts if part).strip()[:SUMMARY_MAX_CHARS]


def _estimate_tokens(chars: int) -> int:
    return max(1, chars // 4) if chars else 0


def _auto_threshold() -> int:
    raw = os.getenv("OPENSRE_SESSION_COMPACTION_CHARS", "").strip()
    if raw.isdigit():
        return max(1_000, int(raw))
    return DEFAULT_AUTO_COMPACTION_CHARS


__all__ = [
    "CompactionResult",
    "DEFAULT_AUTO_COMPACTION_CHARS",
    "auto_compact_if_needed",
    "compact_session_branch",
    "deterministic_summary",
    "should_compact",
]
