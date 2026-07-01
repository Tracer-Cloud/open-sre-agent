"""Per-turn metadata injected into gateway session integration caches."""

from __future__ import annotations

from typing import Any


def inject_gateway_chat_context(resolved: dict[str, Any], chat_id: str) -> dict[str, Any]:
    merged = dict(resolved)
    merged["_gateway_chat_id"] = chat_id
    return merged
