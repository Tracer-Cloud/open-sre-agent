from __future__ import annotations

from datetime import datetime
from enum import Enum

from tools.fleet_monitoring.probe import ProcessSnapshot

_CPU_THRESHOLD_PERCENT = 5.0


class Status(Enum):
    ACTIVE = "active"
    IDLE = "idle"
    STUCK = "stuck"


def compute_status(
    record: ProcessSnapshot,
    now: datetime,
    *,
    last_output_at: datetime | None,
    idle_after_s: int = 120,
    stuck_after_s: int = 480,
) -> Status:
    """
    Classify an agent process as active, idle, or stuck based on CPU activity and output recency.

    When last_output_at is None (no output tracking available), falls back to record.started_at
    as the last-known-activity reference. This may produce false positives for
    stuck on processes that are actively producing output but lack an output tracker.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware (e.g. datetime.now(UTC)), got naive datetime")

    if last_output_at is None:
        last_output_at = record.started_at
    elif last_output_at.tzinfo is None:
        raise ValueError(
            "last_output_at must be timezone-aware (e.g. datetime.now(UTC)), got naive datetime"
        )

    delta = now - last_output_at
    high_cpu = record.cpu_percent >= _CPU_THRESHOLD_PERCENT

    if not high_cpu and delta.total_seconds() >= idle_after_s:
        return Status.IDLE
    if high_cpu and delta.total_seconds() >= stuck_after_s:
        return Status.STUCK

    return Status.ACTIVE
