"""APScheduler-backed blocking runner for scheduled tasks.

Loads all enabled tasks from the store, creates CronTrigger jobs, and
blocks until SIGINT/SIGTERM. Fire times for dedup come from
``JobSubmissionEvent.scheduled_run_times[0]`` (UTC, minute precision),
not wall-clock time inside the callback.
"""

from __future__ import annotations

import logging
import signal
import threading
from datetime import UTC, datetime
from platform.scheduler.executor import execute_task
from platform.scheduler.store import get_task, list_tasks, update_task
from platform.scheduler.types import ScheduledTask
from typing import Any

logger = logging.getLogger(__name__)

# Populated by EVENT_JOB_SUBMITTED before each job runs (job_id -> fire_time).
_pending_fire_times: dict[str, str] = {}
_pending_fire_times_lock = threading.Lock()


def _make_trigger(task: ScheduledTask) -> Any:
    """Build an APScheduler CronTrigger from a task's cron expression and timezone.

    Raises ValueError if the cron expression or timezone is invalid.
    """
    from apscheduler.triggers.cron import CronTrigger

    parts = task.cron.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {task.cron!r}")

    try:
        trigger = CronTrigger.from_crontab(task.cron, timezone=task.timezone)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"Invalid cron/timezone for task {task.id}: {exc}") from exc
    return trigger


def _compute_fire_time(scheduled_run_time: Any) -> str:
    """Compute a stable, UTC-normalized fire_time string.

    Always converts to UTC so DST transitions don't produce ambiguous keys.
    """
    if scheduled_run_time is not None:
        utc_time: datetime = scheduled_run_time.astimezone(UTC)
        return utc_time.strftime("%Y-%m-%dT%H:%MZ")
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")


def _on_job_submitted(event: Any) -> None:
    """Capture the intended fire time for this tick before the job callback runs."""
    run_times = getattr(event, "scheduled_run_times", None)
    if run_times:
        fire_time = _compute_fire_time(run_times[0])
    else:
        fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    with _pending_fire_times_lock:
        _pending_fire_times[event.job_id] = fire_time


def _scheduled_job(task_id: str) -> None:
    """Job callback invoked by APScheduler on each cron tick."""
    with _pending_fire_times_lock:
        fire_time = _pending_fire_times.pop(task_id, None)
    if fire_time is None:
        logger.warning(
            "No scheduled fire_time for task %s; using UTC now (listener may have missed)",
            task_id,
        )
        fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")

    task = get_task(task_id)
    if task is None:
        logger.warning("Task %s not found in store, skipping", task_id)
        return
    if not task.enabled:
        logger.info("Task %s is disabled, skipping", task_id)
        return

    result = execute_task(task, fire_time)

    if result:
        task.last_run = datetime.now(UTC).isoformat()
        update_task(task)


def start_scheduler() -> None:
    """Load all enabled tasks and start the blocking scheduler.

    Blocks until SIGINT or SIGTERM. Invalid tasks (bad cron, bad timezone)
    are logged and skipped rather than crashing the entire daemon.
    """
    from apscheduler.events import EVENT_JOB_SUBMITTED
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_listener(_on_job_submitted, EVENT_JOB_SUBMITTED)

    tasks = list_tasks()
    enabled_count = 0

    for task in tasks:
        if not task.enabled:
            continue
        try:
            trigger = _make_trigger(task)
        except ValueError as exc:
            logger.error("Skipping task %s: %s", task.id, exc)
            continue

        scheduler.add_job(
            _scheduled_job,
            trigger=trigger,
            args=[task.id],
            id=task.id,
            name=f"{task.kind.value}:{task.id}",
            replace_existing=True,
            misfire_grace_time=60,
        )
        enabled_count += 1
        logger.info(
            "Registered task %s (%s) with cron=%s tz=%s",
            task.id,
            task.kind,
            task.cron,
            task.timezone,
        )

    if enabled_count == 0:
        logger.warning("No enabled tasks found. Scheduler has nothing to run.")
        raise SystemExit("No enabled tasks found. Add tasks with `opensre cron add` first.")

    stop_event = threading.Event()

    def _shutdown_handler(_signum: int, _frame: Any) -> None:
        stop_event.set()
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown_handler)
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        signal.signal(sigterm, _shutdown_handler)

    logger.info("Scheduler started with %d task(s). Waiting for triggers...", enabled_count)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


def run_task_now(task_id: str) -> bool:
    """Execute a task immediately (ad-hoc one-shot for debugging).

    Uses the current time with seconds precision as fire_time so it does
    not conflict with scheduled runs (which use minute precision).
    """
    task = get_task(task_id)
    if task is None:
        return False

    fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return execute_task(task, fire_time)


__all__ = ["run_task_now", "start_scheduler"]
