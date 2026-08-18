"""Parse action-agent handoff tags without regex.

Planner tags are ``key:value`` or ``key=value`` on handoff *content strings*
(not user prose). The action prompt documents schema fields with ``=`` and
legacy content tags with ``:``; models often copy either form into ``content``.
"""

from __future__ import annotations

_SEPS = (":", "=")
_TOKEN_LEAD = frozenset("'\"`")
_TOKEN_TRAIL = frozenset(".,;:)]}'\"`")


def _is_tag_boundary(text: str, index: int) -> bool:
    """True when ``index`` may start a ``key`` token (start or after a delimiter)."""
    if index <= 0:
        return True
    prev = text[index - 1]
    return prev.isspace() or prev in "([{\"'`"


def find_tag_suffix(content: str, key: str) -> str | None:
    """Return text after the first boundary ``key:`` / ``key=``, or ``None``.

    Prefers a tag at the start of the stripped string (structured / clean
    legacy form). Otherwise finds the first mid-string boundary match and
    returns the remainder of that line (so ``session_goal_item:…`` may contain
    spaces; ``session_goal:max_turns=5;steps=5`` keeps its body intact).
    """
    text = content.strip()
    if not text or not key:
        return None
    key_l = key.lower()
    for sep in _SEPS:
        prefix = f"{key_l}{sep}"
        if text.lower().startswith(prefix):
            suffix = text[len(prefix) :].strip()
            return suffix or None

    hay_l = text.lower()
    search_from = 0
    while search_from < len(text):
        best_idx: int | None = None
        best_len = 0
        for sep in _SEPS:
            needle = f"{key_l}{sep}"
            idx = hay_l.find(needle, search_from)
            if idx >= 0 and (best_idx is None or idx < best_idx):
                best_idx = idx
                best_len = len(needle)
        if best_idx is None:
            return None
        if not _is_tag_boundary(text, best_idx):
            search_from = best_idx + 1
            continue
        suffix = text[best_idx + best_len :].strip()
        if "\n" in suffix:
            suffix = suffix.split("\n", 1)[0].strip()
        return suffix or None
    return None


def first_tag_token(content: str, key: str) -> str | None:
    """Return the first whitespace-delimited token of :func:`find_tag_suffix`.

    Strips wrapping quotes/backticks and trailing sentence punctuation from the
    token. Used for closed enums such as ``evidence_kind``.
    """
    suffix = find_tag_suffix(content, key)
    if suffix is None:
        return None
    token = suffix.split(None, 1)[0]
    while token and token[0] in _TOKEN_LEAD:
        token = token[1:]
    while token and token[-1] in _TOKEN_TRAIL:
        token = token[:-1]
    return token.lower() if token else None


def handoff_has_tag(content: str, key: str) -> bool:
    """True when ``content`` carries a ``key:`` / ``key=`` handoff tag."""
    return find_tag_suffix(content, key) is not None


__all__ = [
    "find_tag_suffix",
    "first_tag_token",
    "handoff_has_tag",
]
