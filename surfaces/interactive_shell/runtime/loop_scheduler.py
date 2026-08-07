"""Interactive-shell lifecycle wrapper for prompt loop scheduling."""

from __future__ import annotations

import logging
import threading
from typing import Any

from bootstrap.process import SCHEDULED_COMMAND_PROFILE, configure_process
from platform.scheduler.loop_constants import LOOP_PROMPT_PARAM
from platform.scheduler.runner import start_background_scheduler
from platform.scheduler.types import ScheduledTask

log = logging.getLogger(__name__)

_scheduler_lock = threading.Lock()
_scheduler: Any | None = None
_task_count = 0


def _is_prompt_loop_task(task: ScheduledTask) -> bool:
    """Return whether a task is a shell-created recurring prompt loop."""
    return bool(task.params.get(LOOP_PROMPT_PARAM, "").strip())


def start_loop_scheduler() -> int:
    """Start the shell-local prompt-loop scheduler if it is not already running."""
    with _scheduler_lock:
        if _scheduler is not None:
            return _task_count
        return _start_locked()


def reload_loop_scheduler() -> int:
    """Reload prompt-loop jobs after a loop is created or changed.

    Also signals any long-lived gateway scheduler so jobs stay registered after
    this shell exits.
    """
    from platform.scheduler.reload_signal import request_scheduler_reload

    request_scheduler_reload()
    with _scheduler_lock:
        _shutdown_locked()
        return _start_locked()


def shutdown_loop_scheduler() -> None:
    """Stop the shell-local prompt-loop scheduler."""
    with _scheduler_lock:
        _shutdown_locked()


def _start_locked() -> int:
    global _scheduler, _task_count

    configure_process(SCHEDULED_COMMAND_PROFILE)
    scheduler, task_count = start_background_scheduler(task_filter=_is_prompt_loop_task)
    _scheduler = scheduler
    _task_count = task_count
    if scheduler is None:
        log.debug("Interactive shell prompt-loop scheduler idle; no enabled loops.")
    else:
        log.info("Interactive shell prompt-loop scheduler running %d loop(s).", task_count)
    return task_count


def _shutdown_locked() -> None:
    global _scheduler, _task_count

    scheduler = _scheduler
    _scheduler = None
    _task_count = 0
    if scheduler is None:
        return
    try:
        scheduler.shutdown(wait=False)
    except Exception as exc:  # noqa: BLE001
        log.debug("Interactive shell prompt-loop scheduler shutdown failed: %s", exc)


__all__ = [
    "reload_loop_scheduler",
    "shutdown_loop_scheduler",
    "start_loop_scheduler",
]
