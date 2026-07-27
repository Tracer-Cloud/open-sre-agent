"""Shared unavailable-payload helper for investigation tools.

Every tool that can't reach its backend (missing config, failed auth, no
client) returns the same base envelope shape: ``{"source", "available":
False, "error"}``, sometimes with vendor-specific extra keys layered on top.
This module gives that shape one implementation instead of each integration
reconstructing it by hand.
"""

from __future__ import annotations

from typing import Any


def tool_unavailable(source: str, error: str, **extra: Any) -> dict[str, Any]:
    """Return the standard unavailable envelope for a tool that couldn't run.

    ``extra`` is merged in after the base fields so vendor-specific keys
    (e.g. default collections like ``data: []``) can override or extend them.
    """
    return {"source": source, "available": False, "error": error, **extra}
