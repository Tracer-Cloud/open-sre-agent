from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackgroundInvestigationRecord:
    """One completed or in-flight background investigation tracked by the REPL."""

    task_id: str
    status: str
    command: str
    root_cause: str = ""
    top_analysis: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    stats: dict[str, Any] = field(default_factory=dict)
    final_state: dict[str, Any] = field(default_factory=dict)
    notification_results: dict[str, str] = field(default_factory=dict)


@dataclass
class BackgroundNotificationPreferences:
    """Session-scoped channel preferences for background RCA completion notifications."""

    channels: tuple[str, ...] = ()

    def set_channels(self, values: list[str]) -> None:
        cleaned: list[str] = []
        for value in values:
            normalized = value.strip().lower()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        self.channels = tuple(cleaned)
