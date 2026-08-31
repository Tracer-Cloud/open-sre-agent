"""Lightweight KQL text helpers for row-capping pipe-stage detection.

Not a full KQL parser -- masks quoted string literals and ``//`` line
comments so row-cap pipe-stage detection isn't fooled by text that
merely *contains* those keywords inside a string or comment (e.g. a
``where Message contains "| take 5"`` filter). Handles both of KQL's
string forms: regular strings (``"..."``/``'...'``, backslash-escaped
quotes) and verbatim strings (``@"..."``/``@'...'``, where backslash is
a literal character and a doubled quote is the only escape) -- treating
a verbatim string as a regular one would let a backslash before its
real closing quote extend the mask past the string's true end, hiding
a real row-cap stage that follows it.
"""

from __future__ import annotations

import re

#: KQL row-capping pipe stages: ``limit`` is a synonym for ``take``;
#: ``sample N`` returns up to N rows; ``top N by ...`` returns the top N by
#: a sort expression. All four bound the row count the same way for the
#: purpose of saturation detection. Require a preceding ``|`` (the actual
#: pipe-stage syntax) against the masked query so a match can only come
#: from a real operator, never from string/comment text.
_ROW_CAP_CLAUSE_RE = re.compile(
    r"\|\s*(?:take|limit|sample)\s+(\d+)\b|\|\s*top\s+(\d+)\s+by\b", re.IGNORECASE
)


def mask_string_and_comment_text(query: str) -> str:
    """Blank out quoted-string contents and ``//`` line comments.

    Preserves length and newlines so a masked query is still safe to scan
    with position-sensitive logic; only content that could hide a fake
    pipe-stage match is replaced with spaces.
    """
    result: list[str] = []
    quote_char: str | None = None
    verbatim = False
    i = 0
    n = len(query)
    while i < n:
        ch = query[i]
        if quote_char is not None:
            if not verbatim and ch == "\\" and i + 1 < n:
                # A backslash-escaped character (``\"``, ``\\``, ...) in a
                # *regular* string can't close the string or be interpreted
                # as real syntax -- mask both the backslash and the escaped
                # character together, so an escaped quote never ends the
                # string early. Verbatim strings don't use this escape --
                # a backslash there is just a literal character.
                next_ch = query[i + 1]
                result.append(ch if ch == "\n" else " ")
                result.append(next_ch if next_ch == "\n" else " ")
                i += 2
                continue
            if ch == quote_char:
                # A verbatim string escapes a literal quote by doubling it
                # (``""``/``''``), not with a backslash -- a doubled quote
                # here isn't the closing quote.
                if verbatim and i + 1 < n and query[i + 1] == quote_char:
                    result.append(ch)
                    result.append(query[i + 1])
                    i += 2
                    continue
                quote_char = None
                verbatim = False
                result.append(ch)
            elif ch == "\n":
                result.append(ch)
            else:
                result.append(" ")
            i += 1
            continue
        if ch == "@" and i + 1 < n and query[i + 1] in ("'", '"'):
            verbatim = True
            quote_char = query[i + 1]
            result.append(ch)
            result.append(query[i + 1])
            i += 2
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


def has_row_cap_clause(query: str) -> bool:
    """True if ``query`` contains a real (non-string/comment) row-cap pipe stage."""
    return bool(_ROW_CAP_CLAUSE_RE.search(mask_string_and_comment_text(query)))


def find_row_cap_values(query: str) -> list[int]:
    """Return the integer caps from every real row-cap pipe stage in ``query``."""
    masked = mask_string_and_comment_text(query)
    return [int(g1 or g2) for g1, g2 in _ROW_CAP_CLAUSE_RE.findall(masked)]
