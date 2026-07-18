"""Background process watchdog loop (REPL and CLI)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from platform.common.task_types import TaskRecord, TaskStatus
from tools.system.fleet_monitoring.probe import pid_exists, probe


@runtime_checkable
class AlarmDispatcherPort(Protocol):
    """Minimal alarm-dispatch contract the watchdog depends on.

    The concrete implementation lives in
    :class:`integrations.telegram.alarms.AlarmDispatcher`, but the watchdog
    stays behind this local protocol so the module never imports
    ``integrations.*`` directly (T-4 layering audit, issue #3352, item 37).
    Callers wire the concrete dispatcher in at the CLI entry point.
    """

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Dispatch one threshold alarm. Returns ``True`` when the alert fired."""


def run_watchdog(
    *,
    task: TaskRecord,
    watched_pid: int,
    interval_seconds: float,
    max_cpu: float | None,
    max_runtime_seconds: float | None,
    max_rss_mib: float | None,
    once: bool,
    dispatcher: AlarmDispatcherPort,
    on_alarm: Callable[[str, str], None] | None,
) -> None:
    """Poll ``watched_pid`` until cancel, process exit, or optional single alarm.

    Updates ``task`` progress each tick; dispatches Telegram on threshold breach.
    ``interval_seconds`` is both the inter-sample delay and the CPU percent window
    passed to :func:`~tools.system.fleet_monitoring.probe.probe` (floored for stability).
    With ``once`` and no threshold flags, completes after the first successful sample.
    """
    sample_interval = max(float(interval_seconds), 0.1)

    def _notify_alarm(threshold: str, detail: str) -> None:
        if on_alarm is not None:
            on_alarm(threshold, detail)

    first_sample = True
    try:
        while True:
            if task.cancel_requested.is_set():
                task.mark_cancelled()
                return

            snap = probe(watched_pid, cpu_interval=sample_interval)
            if snap is None:
                if task.cancel_requested.is_set():
                    task.mark_cancelled()
                    return
                if pid_exists(watched_pid):
                    # Alive but not introspectable (e.g. another user's
                    # process, macOS hardened runtime). A permission
                    # failure is not an exit: fail loudly up front, retry
                    # when it appears mid-watch.
                    if first_sample:
                        task.mark_failed(f"cannot introspect pid {watched_pid} (permission denied)")
                        return
                    task.update_progress("no snapshot this tick (permission denied); retrying")
                    time.sleep(sample_interval)
                    continue
                if task.status == TaskStatus.RUNNING:
                    task.mark_completed(result="target process exited")
                return
            first_sample = False

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
    dispatcher: AlarmDispatcherPort,
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


__all__ = ["AlarmDispatcherPort", "run_watchdog", "start_watchdog_daemon_thread"]
