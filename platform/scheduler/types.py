"""Domain models for the scheduled-delivery subsystem."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class TaskKind(StrEnum):
    """Supported scheduled task kinds."""

    DAILY_SUMMARY = "daily_summary"
    WEEKLY_AUDIT = "weekly_audit"
    INCIDENT_WINDOW_REPLAY = "incident_window_replay"
    SYNTHETIC_RUN = "synthetic_run"
    CUSTOM_INVESTIGATION = "custom_investigation"
    SENTRY_MORNING_DIGEST = "sentry_morning_digest"


class TaskStatus(StrEnum):
    """Execution status for a single task run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class Provider(StrEnum):
    """Supported messaging providers for delivery."""

    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"


def _generate_task_id() -> str:
    return uuid.uuid4().hex[:12]


class ScheduledTask(BaseModel):
    """A persisted scheduled-task definition."""

    id: str = Field(default_factory=_generate_task_id)
    kind: TaskKind
    cron: str
    timezone: str = "UTC"
    provider: Provider
    chat_id: str = ""
    window_hours: int = 24
    enabled: bool = True
    params: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_run: str | None = None
    next_run: str | None = None

    def display_id(self) -> str:
        """Short display ID for CLI output."""
        return self.id[:12]


class TaskRun(BaseModel):
    """A single execution record for a scheduled task."""

    task_id: str
    fire_time: str
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    posted_message_id: str = ""
    error: str = ""
    provider: str = ""


__all__ = [
    "Provider",
    "ScheduledTask",
    "TaskKind",
    "TaskRun",
    "TaskStatus",
]
