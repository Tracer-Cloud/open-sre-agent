"""Pure text classifiers for interactive-shell prompt turns."""

from __future__ import annotations

import re

_INTERVENTION_CORRECTION_RE = re.compile(
    r"("
    r"no(?=[,.!?]|$)"
    r"|nope\b"
    r"|nvm\b"
    r"|nevermind\b|never\s*mind\b"
    r"|wrong\b"
    r"|wait(?=[,.!?]|$)"
    r"|stop(?=[,.!?]|$)"
    r"|actually\b"
    r"|scratch\s+that\b"
    r"|instead(?=[,.!?]|$)"
    r"|(?:let'?s\s+)?do\s+[^.\n]{1,60}\s+instead\b"
    r"|try\s+[^.\n]{1,60}\s+instead\b"
    r")",
    re.IGNORECASE,
)
_CONFIRMATION_TOKENS: frozenset[str] = frozenset({"", "y", "yes", "n", "no"})
_CANCEL_REQUEST_TOKENS: frozenset[str] = frozenset({"/cancel", "/stop", "/abort"})


def looks_like_confirmation_answer(text: str | None) -> bool:
    return (text or "").strip().lower() in _CONFIRMATION_TOKENS


def looks_like_cancel_request(text: str | None) -> bool:
    return (text or "").strip().lower() in _CANCEL_REQUEST_TOKENS


def looks_like_correction(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped or stripped.startswith("```"):
        return False
    return _INTERVENTION_CORRECTION_RE.match(stripped[:80]) is not None


__all__ = [
    "looks_like_cancel_request",
    "looks_like_confirmation_answer",
    "looks_like_correction",
]
