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


def closer_tail_from(content: str) -> str | None:
    """Return the Want-me-to closer paragraph (markdown-aware), if present.

    Used when gather streams defer the closer, then paints the canonical
    rewrite after ``finalize_gather_investigation_offer``.
    """
    if not isinstance(content, str) or not content:
        return None
    lowered = content.lower()
    pos = lowered.rfind(WANT_ME_TO_MARKER)
    if pos < 0:
        return None
    start = pos
    # Include a leading ``**`` so ``**Want me to:**`` renders intact.
    if start >= 2 and content[start - 2 : start] == "**":
        start -= 2
    elif start >= 1 and content[start - 1] == "*":
        # tolerate a single stray star
        while start > 0 and content[start - 1] == "*":
            start -= 1
    tail = content[start:].strip()
    blank = tail.find("\n\n")
    if blank >= 0:
        tail = tail[:blank].rstrip()
    return tail or None


def replace_want_me_to_body(content: str, new_body: str) -> str:
    """Replace the newest Want-me-to offer body, or append one if missing.

    Keeps prose before the closer. Used after gather answers so dual
    paste/integrations Want-me-to menus become a single canonical investigate
    offer the accept path can arm.
    """
    body = (new_body or "").strip()
    if not body:
        return content if isinstance(content, str) else ""
    text = content if isinstance(content, str) else ""
    closer = f"**Want me to:** {body}"
    if not text.strip():
        return closer

    lowered = text.lower()
    pos = lowered.rfind(WANT_ME_TO_MARKER)
    if pos < 0:
        return f"{text.rstrip()}\n\n{closer}"

    head = text[:pos].rstrip()
    while head.endswith("*"):
        head = head[:-1]
    head = head.rstrip()

    rest = text[pos + len(WANT_ME_TO_MARKER) :]
    rest = rest.lstrip()
    if rest.startswith("**"):
        rest = rest[2:].lstrip()
    blank = rest.find("\n\n")
    after = rest[blank:] if blank >= 0 else ""
    if head:
        return f"{head}\n\n{closer}{after}"
    return f"{closer}{after}"


__all__ = [
    "WANT_ME_TO_MARKER",
    "closer_tail_from",
    "offer_from_assistant_content",
    "replace_want_me_to_body",
]
