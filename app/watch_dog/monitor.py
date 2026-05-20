"""Background process watchdog loop (REPL and CLI)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from app.agents.probe import probe
from app.cli.interactive_shell.runtime.tasks import TaskRecord, TaskStatus
from app.watch_dog.alarms import AlarmDispatcher


def run_watchdog(
    *,
    task: TaskRecord,
    watched_pid: int,
    interval_seconds: float,
    max_cpu: float | None,
    max_runtime_seconds: float | None,
    max_rss_mib: float | None,
    once: bool,
    dispatcher: AlarmDispatcher,
    on_alarm: Callable[[str, str], None] | None,
) -> None:
    """Poll ``watched_pid`` until cancel, process exit, or optional single alarm.

    Updates ``task`` progress each tick; dispatches Telegram on threshold breach.
    ``interval_seconds`` is both the inter-sample delay and the CPU percent window
    passed to :func:`~app.agents.probe.probe` (floored for stability).
    With ``once`` and no threshold flags, completes after the first successful sample.
    """
    sample_interval = max(float(interval_seconds), 0.1)

    def _notify_alarm(threshold: str, detail: str) -> None:
        if on_alarm is not None:
            on_alarm(threshold, detail)

    try:
        while True:
            if task.cancel_requested.is_set():
                task.mark_cancelled()
                return

            snap = probe(watched_pid, cpu_interval=sample_interval)
            if snap is None:
                if task.cancel_requested.is_set():
                    task.mark_cancelled()
                elif task.status == TaskStatus.RUNNING:
                    task.mark_completed(result="target process exited")
                return

            runtime_s = max(0.0, (datetime.now(UTC) - snap.started_at).total_seconds())
            progress = (
                f"cpu={snap.cpu_percent:.1f}% rss={snap.rss_mb:.1f}MiB runtime={runtime_s:.0f}s"
            )
            task.update_progress(progress)

            fired_once = False
            if max_cpu is not None and snap.cpu_percent >= max_cpu:
                detail = f"{snap.cpu_percent:.1f}% (threshold {max_cpu:g}%)"
                msg = (
                    f"OpenSRE watchdog: PID {watched_pid} exceeded max_cpu — {detail} "
                    f"(task {task.task_id})"
                )
                if dispatcher.dispatch("max_cpu", msg):
                    _notify_alarm("max_cpu", detail)
                    fired_once = True
            if max_rss_mib is not None and snap.rss_mb >= max_rss_mib:
                detail = f"{snap.rss_mb:.1f}MiB (threshold {max_rss_mib:g}MiB)"
                msg = (
                    f"OpenSRE watchdog: PID {watched_pid} exceeded max_rss — {detail} "
                    f"(task {task.task_id})"
                )
                if dispatcher.dispatch("max_rss", msg):
                    _notify_alarm("max_rss", detail)
                    fired_once = True
            if max_runtime_seconds is not None and runtime_s >= max_runtime_seconds:
                detail = f"runtime {runtime_s:.0f}s (threshold {max_runtime_seconds:g}s)"
                msg = (
                    f"OpenSRE watchdog: PID {watched_pid} exceeded max_runtime — {detail} "
                    f"(task {task.task_id})"
                )
                if dispatcher.dispatch("max_runtime", msg):
                    _notify_alarm("max_runtime", detail)
                    fired_once = True

            any_threshold = (
                max_cpu is not None or max_rss_mib is not None or max_runtime_seconds is not None
            )
            if once:
                if fired_once:
                    task.mark_completed(result="alarm (once)")
                    return
                if not any_threshold:
                    task.mark_completed(result="single sample (once)")
                    return

            if task.cancel_requested.is_set():
                task.mark_cancelled()
                return
    except Exception as exc:  # noqa: BLE001
        if task.status == TaskStatus.RUNNING:
            task.mark_failed(str(exc))


def start_watchdog_daemon_thread(
    *,
    task: TaskRecord,
    watched_pid: int,
    interval_seconds: float,
    max_cpu: float | None,
    max_runtime_seconds: float | None,
    max_rss_mib: float | None,
    once: bool,
    dispatcher: AlarmDispatcher,
    on_alarm: Callable[[str, str], None] | None,
) -> threading.Thread:
    """Start :func:`run_watchdog` on a daemon thread named ``watchdog-<task_id>``."""

    thread = threading.Thread(
        target=run_watchdog,
        kwargs={
            "task": task,
            "watched_pid": watched_pid,
            "interval_seconds": interval_seconds,
            "max_cpu": max_cpu,
            "max_runtime_seconds": max_runtime_seconds,
            "max_rss_mib": max_rss_mib,
            "once": once,
            "dispatcher": dispatcher,
            "on_alarm": on_alarm,
        },
        daemon=True,
        name=f"watchdog-{task.task_id}",
    )
    thread.start()
    return thread


__all__ = ["run_watchdog", "start_watchdog_daemon_thread"]
