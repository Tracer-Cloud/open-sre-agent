"""Small helpers for human-friendly slash-command suggestions."""

from __future__ import annotations

from difflib import get_close_matches


def closest_choice(value: str, choices: list[str] | tuple[str, ...]) -> str | None:
    """Return the nearest command-like choice for a typo, if confidence is high enough."""
    normalized = value.strip().lower()
    if not normalized:
        return None
    matches = get_close_matches(normalized, choices, n=1, cutoff=0.72)
    return matches[0] if matches else None


__all__ = ["closest_choice"]
