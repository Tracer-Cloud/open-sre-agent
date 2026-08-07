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
    SENTRY_UPTIME_WATCH = "sentry_uptime_watch"
    GITHUB_PR_SWEEP = "github_pr_sweep"
    POSTHOG_METRIC_REPORT = "posthog_metric_report"
    WORK_ITEM_REMINDER = "work_item_reminder"
    WORK_ITEM_CHECKIN = "work_item_checkin"


class TaskStatus(StrEnum):
    """Execution status for a single task run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class Provider(StrEnum):
    """The canonical delivery-provider vocabulary: where a scheduled outbound
    message (cron digest, watchdog alarm, ...) can be sent.

    Distinct from ``integrations.messaging_security.MessagingPlatform``,
    which tracks gateway *inbound* identity, not delivery. Not every consumer
    supports every member here (e.g. Sentry digest delivery has no Discord
    path, watchdog alarms only support Telegram/Rocket.Chat/Buzz) -- those
    consumers define their own documented subset rather than exposing a
    choice that would silently fail.
    """

    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"
    ROCKETCHAT = "rocketchat"
    INTERACTIVE_SHELL = "interactive_shell"
    BUZZ = "buzz"


def _generate_task_id() -> str:
    return uuid.uuid4().hex[:12]


class ScheduledTask(BaseModel):
    """A persisted scheduled-task definition."""

    id: str = Field(default_factory=_generate_task_id)
    name: str = ""
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
