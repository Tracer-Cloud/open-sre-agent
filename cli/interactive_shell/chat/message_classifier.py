"""Message intent classification for the terminal assistant."""

from __future__ import annotations

from pathlib import Path

from cli.interactive_shell.runtime.session import SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST

_MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS = 120_000


def _user_message_requests_synthetic_failure_explanation(message: str) -> bool:
    """True when the user is likely asking about a failed synthetic benchmark."""
    m = message.strip().lower()
    if not m:
        return False
    suggested = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST.lower().rstrip("?")
    if m.rstrip("?") == suggested:
        return True
    if "why" in m and "fail" in m:
        return True
    return "what went wrong" in m


def _load_synthetic_observation_text(
    path_str: str, *, max_chars: int = _MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS
) -> str:
    try:
        raw = Path(path_str).read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(raw) > max_chars:
        return (
            raw[:max_chars]
            + f"\n… [truncated for prompt size; observation is {len(raw)} characters total]"
        )
    return raw


__all__ = [
    "_load_synthetic_observation_text",
    "_user_message_requests_synthetic_failure_explanation",
]
