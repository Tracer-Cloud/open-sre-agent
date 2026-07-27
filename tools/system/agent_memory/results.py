"""Result shaping for the agent memory tools.

Every result includes ``path`` (or the memory directory) so writes stay
visible and auditable to the user; expected failures come back as structured
``error`` dicts rather than exceptions.
"""

from __future__ import annotations

from typing import Any

from core.domain.memory import MemoryRecord, memory_path

RECALL_BODY_CHAR_CAP = 4_000
RECALL_TOTAL_CHAR_CAP = 12_000


def saved_result(record: MemoryRecord, *, created: bool) -> dict[str, Any]:
    return {
        "status": "created" if created else "updated",
        "name": record.slug,
        "type": record.memory_type,
        "description": record.description,
        "path": str(memory_path(record.slug)),
    }


def deleted_result(slug: str, *, deleted: bool) -> dict[str, Any]:
    return {
        "status": "deleted" if deleted else "not_found",
        "name": slug,
        "path": str(memory_path(slug)),
    }


def recall_result(records: list[MemoryRecord], *, total_stored: int) -> dict[str, Any]:
    memories: list[dict[str, Any]] = []
    remaining = RECALL_TOTAL_CHAR_CAP
    for record in records:
        body = record.body[: min(RECALL_BODY_CHAR_CAP, max(remaining, 0))]
        if len(body) < len(record.body):
            body += "\n...[truncated]"
        memories.append(
            {
                "name": record.slug,
                "type": record.memory_type,
                "description": record.description,
                "updated": record.updated_at,
                "content": body,
            }
        )
        remaining -= len(body)
        if remaining <= 0:
            break
    return {"memories": memories, "total_stored": total_stored}


def index_result(records: list[MemoryRecord]) -> dict[str, Any]:
    return {
        "memories": [
            {
                "name": record.slug,
                "type": record.memory_type,
                "description": record.description,
                "updated": record.updated_at,
            }
            for record in records
        ],
        "total_stored": len(records),
    }


__all__ = [
    "RECALL_BODY_CHAR_CAP",
    "RECALL_TOTAL_CHAR_CAP",
    "deleted_result",
    "index_result",
    "recall_result",
    "saved_result",
]
