"""Want-me-to closer extraction — no session types, no pending-offer imports.

Keeps prompt/memory and pending-offer modules from importing each other.
"""

from __future__ import annotations

WANT_ME_TO_MARKER = "want me to:"


def offer_from_assistant_content(content: str) -> str | None:
    """Extract one Want-me-to offer body from a single assistant message, if any."""
    if not isinstance(content, str) or not content:
        return None
    lowered = content.lower()
    pos = lowered.rfind(WANT_ME_TO_MARKER)
    if pos < 0:
        return None
    rest = content[pos + len(WANT_ME_TO_MARKER) :].lstrip()
    if rest.startswith("**"):
        rest = rest[2:].lstrip()
    blank = rest.find("\n\n")
    if blank >= 0:
        rest = rest[:blank]
    offer = rest.strip().rstrip("?").strip()
    return offer or None


__all__ = ["WANT_ME_TO_MARKER", "offer_from_assistant_content"]
