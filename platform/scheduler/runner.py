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
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from platform.scheduler.executor import execute_task
from platform.scheduler.store import get_task, list_tasks, update_task
from platform.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)
TaskFilter = Callable[[ScheduledTask], bool]

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


def _next_run_from_trigger(trigger: Any, now: datetime | None = None) -> str | None:
    """Return the next UTC fire time for an already-built trigger."""
    base = now or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    next_fire = cast(datetime | None, trigger.get_next_fire_time(None, base))
    if next_fire is None:
        return None
    return next_fire.astimezone(UTC).isoformat()


def compute_next_run(task: ScheduledTask, now: datetime | None = None) -> str | None:
    """Return the task's next UTC cron fire time, or raise for invalid schedules."""
    return _next_run_from_trigger(_make_trigger(task), now)


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
        if task.params.get("disable_after_success", "").strip().lower() == "true":
            task.enabled = False
        update_task(task)


def _register_jobs(
    scheduler: Any,
    *,
    task_filter: TaskFilter | None = None,
    add_listener: bool = True,
) -> int:
    """Register all enabled tasks on *scheduler*; invalid tasks are logged and skipped."""
    if add_listener:
        from apscheduler.events import EVENT_JOB_SUBMITTED

        scheduler.add_listener(_on_job_submitted, EVENT_JOB_SUBMITTED)

    enabled_count = 0
    for task in list_tasks():
        if not task.enabled:
            continue
        if task_filter is not None and not task_filter(task):
            continue
        try:
            trigger = _make_trigger(task)
        except ValueError as exc:
            logger.error("Skipping task %s: %s", task.id, exc)
            continue
        next_run = _next_run_from_trigger(trigger)
        if task.next_run != next_run:
            task.next_run = next_run
            update_task(task)

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
    return enabled_count


def _desired_task_ids(*, task_filter: TaskFilter | None = None) -> set[str]:
    """Return enabled task ids that should be registered under ``task_filter``."""
    desired: set[str] = set()
    for task in list_tasks():
        if not task.enabled:
            continue
        if task_filter is not None and not task_filter(task):
            continue
        desired.add(task.id)
    return desired


def resync_scheduler_jobs(
    scheduler: Any,
    *,
    task_filter: TaskFilter | None = None,
) -> int:
    """Replace registered jobs on a live scheduler with the current task store."""
    existing_ids = {job.id for job in scheduler.get_jobs()}
    enabled_count = _register_jobs(
        scheduler,
        task_filter=task_filter,
        add_listener=False,
    )
    desired_ids = _desired_task_ids(task_filter=task_filter)
    for job_id in existing_ids - desired_ids:
        try:
            scheduler.remove_job(job_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to remove stale scheduler job %s: %s", job_id, exc)
    logger.info("Scheduler resynced with %d enabled task(s)", enabled_count)
    return enabled_count


def refresh_background_scheduler(
    scheduler: Any | None,
    *,
    task_filter: TaskFilter | None = None,
) -> tuple[Any | None, int]:
    """Resync ``scheduler`` or start one when the store gained its first task.

    Returns ``(scheduler, task_count)``. When every task is disabled/removed the
    existing scheduler is shut down and ``None`` is returned.
    """
    if scheduler is None:
        return start_background_scheduler(task_filter=task_filter)

    enabled_count = resync_scheduler_jobs(scheduler, task_filter=task_filter)
    if enabled_count > 0:
        return scheduler, enabled_count

    try:
        scheduler.shutdown(wait=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Scheduler shutdown after empty resync failed: %s", exc)
    return None, 0


def start_background_scheduler(
    *,
    task_filter: TaskFilter | None = None,
) -> tuple[Any, int]:
    """Start a non-blocking scheduler for embedding in a host process.

    Installs no signal handlers and never exits the process. Returns
    ``(scheduler, task_count)``; the scheduler is ``None`` when there are no
    enabled tasks. The caller owns shutdown via ``scheduler.shutdown()``.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    enabled_count = _register_jobs(scheduler, task_filter=task_filter)
    if enabled_count == 0:
        return None, 0
    scheduler.start()
    logger.info("Scheduler started with %d task(s). Waiting for triggers...", enabled_count)
    return scheduler, enabled_count


def start_scheduler() -> None:
    """Load all enabled tasks and start the blocking scheduler.

    Blocks until SIGINT or SIGTERM. Invalid tasks (bad cron, bad timezone)
    are logged and skipped rather than crashing the entire daemon.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    enabled_count = _register_jobs(scheduler)
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


__all__ = [
    "compute_next_run",
    "refresh_background_scheduler",
    "resync_scheduler_jobs",
    "run_task_now",
    "start_background_scheduler",
    "start_scheduler",
]
