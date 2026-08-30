"""Lightweight KQL text helpers for take/limit pipe-stage detection.

Not a full KQL parser -- masks quoted string literals and ``//`` line
comments so take/limit pipe-stage detection isn't fooled by text that
merely *contains* those keywords inside a string or comment (e.g. a
``where Message contains "| take 5"`` filter).
"""

from __future__ import annotations

import re

#: KQL defines ``limit`` as an alias for ``take``. Require a preceding ``|``
#: (the actual pipe-stage syntax) against the masked query so a match can
#: only come from a real operator, never from string/comment text.
_TAKE_OR_LIMIT_CLAUSE_RE = re.compile(r"\|\s*(?:take|limit)\s+(\d+)\b", re.IGNORECASE)


def mask_string_and_comment_text(query: str) -> str:
    """Blank out quoted-string contents and ``//`` line comments.

    Preserves length and newlines so a masked query is still safe to scan
    with position-sensitive logic; only content that could hide a fake
    pipe-stage match is replaced with spaces.
    """
    result: list[str] = []
    quote_char: str | None = None
    i = 0
    n = len(query)
    while i < n:
        ch = query[i]
        if quote_char is not None:
            if ch == quote_char:
                quote_char = None
                result.append(ch)
            elif ch == "\n":
                result.append(ch)
            else:
                result.append(" ")
            i += 1
            continue
        if ch in ("'", '"'):
            quote_char = ch
            result.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and query[i + 1] == "/":
            while i < n and query[i] != "\n":
                result.append(" ")
                i += 1
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def has_take_or_limit_clause(query: str) -> bool:
    """True if ``query`` contains a real (non-string/comment) take/limit pipe stage."""
    return bool(_TAKE_OR_LIMIT_CLAUSE_RE.search(mask_string_and_comment_text(query)))


def find_take_or_limit_values(query: str) -> list[int]:
    """Return the integer caps from every real take/limit pipe stage in ``query``."""
    return [int(m) for m in _TAKE_OR_LIMIT_CLAUSE_RE.findall(mask_string_and_comment_text(query))]
