"""One shape for a session history row.

A leaf module: both :class:`SessionCore` and the headless session adapter append
rows that tools read back, so the shape has to be defined once rather than
mirrored.
"""

from __future__ import annotations

from typing import Any


def build_history_entry(
    kind: str,
    text: str,
    *,
    ok: bool = True,
    response_text: str | None = None,
    slash_outcome: str | None = None,
) -> dict[str, Any]:
    """Build one history row, omitting optional fields that carry no value.

    Absent beats present-and-empty: readers check membership, so an empty
    ``response_text`` would look like a recorded-but-blank reply.
    """
    optional = {"response_text": response_text, "slash_outcome": slash_outcome}
    return {
        "type": kind,
        "text": text,
        "ok": ok,
        **{field: value for field, value in optional.items() if value},
    }


__all__ = ["build_history_entry"]
