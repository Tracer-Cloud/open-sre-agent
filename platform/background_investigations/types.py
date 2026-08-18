"""Background-investigation value types shared across surfaces."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def coerce_timestamp(value: object) -> float:
    """Return a finite timestamp, or ``0.0`` for invalid persisted data."""
    if not isinstance(value, int | float):
        return 0.0
    try:
        coerced = float(value)
    except OverflowError:
        return 0.0
    return coerced if math.isfinite(coerced) else 0.0


@dataclass
class BackgroundInvestigationRecord:
    """One completed or in-flight background investigation."""

    task_id: str
    status: str
    command: str
    investigation_id: str = ""
    root_cause: str = ""
    top_analysis: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    stats: dict[str, Any] = field(default_factory=dict)
    final_state: dict[str, Any] = field(default_factory=dict)
    notification_results: dict[str, str] = field(default_factory=dict)
    # Epoch seconds, stamped by the store on write. Not default_factory=time.time:
    # a wall-clock default makes two otherwise identical records unequal.
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize persisted fields, omitting the unbounded ``final_state``."""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "command": self.command,
            "investigation_id": self.investigation_id,
            "root_cause": self.root_cause,
            "top_analysis": list(self.top_analysis),
            "next_steps": list(self.next_steps),
            "stats": dict(self.stats),
            "notification_results": dict(self.notification_results),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BackgroundInvestigationRecord | None:
        """Rebuild a record, returning ``None`` when required fields are invalid."""
        task_id = data.get("task_id")
        status = data.get("status")
        command = data.get("command")
        if not isinstance(task_id, str) or not isinstance(status, str):
            return None
        if not isinstance(command, str):
            return None

        def _text(key: str) -> str:
            value = data.get(key)
            return value if isinstance(value, str) else ""

        def _items(key: str) -> tuple[str, ...]:
            value = data.get(key)
            if not isinstance(value, list):
                return ()
            return tuple(str(item) for item in value)

        def _mapping(key: str) -> dict[str, Any]:
            value = data.get(key)
            return dict(value) if isinstance(value, Mapping) else {}

        return cls(
            task_id=task_id,
            status=status,
            command=command,
            investigation_id=_text("investigation_id"),
            root_cause=_text("root_cause"),
            top_analysis=_items("top_analysis"),
            next_steps=_items("next_steps"),
            stats=_mapping("stats"),
            notification_results={
                str(k): str(v) for k, v in _mapping("notification_results").items()
            },
            created_at=coerce_timestamp(data.get("created_at")),
            updated_at=coerce_timestamp(data.get("updated_at")),
        )


__all__ = ["BackgroundInvestigationRecord", "coerce_timestamp"]
