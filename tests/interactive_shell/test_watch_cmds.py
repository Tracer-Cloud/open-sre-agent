"""Tests for /watch, /watches, /unwatch slash commands."""

from __future__ import annotations

import io
import threading
import time
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from integrations.telegram.credentials import TelegramCredentials
from platform.common.task_types import TaskKind, TaskStatus
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.command_registry.watch_cmds import (
    WatchdogStartSpec,
    parse_watch_argv,
)
from surfaces.interactive_shell.session import Session


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_slash_registry_includes_watchdog_commands() -> None:
    assert "/watch" in SLASH_COMMANDS
    assert "/watches" in SLASH_COMMANDS
    assert "/unwatch" in SLASH_COMMANDS


def test_parse_watch_argv_rejects_non_numeric_pid() -> None:
    out = parse_watch_argv(["not-a-pid"])
    assert isinstance(out, str)
    assert "invalid pid" in out


def test_parse_watch_argv_rejects_non_positive_pid() -> None:
    out = parse_watch_argv(["0"])
    assert isinstance(out, str)
    assert "positive" in out


def test_parse_watch_argv_parses_flags() -> None:
    raw = parse_watch_argv(["999", "--max-cpu", "80", "--max-runtime", "10s", "--once"])
    assert isinstance(raw, WatchdogStartSpec)
    assert raw.pid == 999
    assert raw.max_cpu == 80.0
    assert raw.max_runtime_seconds == 10.0
    assert raw.once is True


def test_dispatch_watch_creates_watchdog_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.watch_cmds.load_credentials_from_env",
        lambda *_a, **_kw: TelegramCredentials(bot_token="x", chat_id="1"),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.watch_cmds.pid_exists",
        lambda _pid: True,
    )

    def _fake_start(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.watch_cmds.start_watchdog_daemon_thread",
        _fake_start,
    )

    session = Session()
    session.terminal.trust_mode = True
    console, buf = _capture()
    dispatch_slash(
        f"/watch {__import__('os').getpid()} --max-cpu 80",
        session,
        console,
        is_tty=True,
    )

    watchdogs = [t for t in session.task_registry.list_recent(20) if t.kind == TaskKind.WATCHDOG]
    assert len(watchdogs) == 1
    assert watchdogs[0].status == TaskStatus.RUNNING
    assert "max_cpu=80" in (watchdogs[0].command or "")
    assert "started" in buf.getvalue()


def test_unwatch_marks_watchdog_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.watch_cmds.load_credentials_from_env",
        lambda *_a, **_kw: TelegramCredentials(bot_token="x", chat_id="1"),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.watch_cmds.pid_exists",
        lambda _pid: True,
    )

    barrier = threading.Event()

    def _slow_watchdog(**kwargs: object) -> None:
        task = kwargs["task"]
        barrier.set()
        while not task.cancel_requested.is_set():
            time.sleep(0.02)
        task.mark_cancelled()

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.watch_cmds.start_watchdog_daemon_thread",
        lambda **kw: threading.Thread(target=_slow_watchdog, kwargs=kw, daemon=True).start(),
    )

    session = Session()
    session.terminal.trust_mode = True
    console, _ = _capture()
    dispatch_slash(
        f"/watch {__import__('os').getpid()} --interval 1s",
        session,
        console,
        is_tty=True,
    )
    assert barrier.wait(timeout=2.0), "watchdog thread should start"
    task = next(t for t in session.task_registry.list_recent(20) if t.kind == TaskKind.WATCHDOG)
    dispatch_slash(f"/unwatch {task.task_id}", session, console, is_tty=True)
    for _ in range(200):
        task.refresh_rehydrated_status()
        if task.status == TaskStatus.CANCELLED:
            break
        time.sleep(0.02)
    assert task.status == TaskStatus.CANCELLED


def test_unwatch_rejects_non_watchdog_task() -> None:
    session = Session()
    session.terminal.trust_mode = True
    inv = session.task_registry.create(TaskKind.INVESTIGATION, command="x")
    inv.mark_running()
    console, buf = _capture()
    dispatch_slash(f"/unwatch {inv.task_id}", session, console, is_tty=True)
    assert "not a watchdog" in buf.getvalue()


def test_run_watchdog_respects_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime, timedelta

    from platform.common.task_registry import TaskRegistry
    from tools.system.fleet_monitoring.probe import ProcessSnapshot
    from tools.system.watch_dog.monitor import run_watchdog

    reg = TaskRegistry()
    task = reg.create(TaskKind.WATCHDOG, command="watchdog pid=1")
    task.mark_running()
    dispatcher = MagicMock()
    dispatcher.dispatch = MagicMock(return_value=True)

    started_at = datetime.now(UTC) - timedelta(seconds=5)
    snap = ProcessSnapshot(
        pid=1,
        cpu_percent=1.0,
        rss_mb=10.0,
        num_fds=None,
        num_connections=None,
        status="running",
        started_at=started_at,
    )

    monkeypatch.setattr("tools.system.watch_dog.monitor.probe", lambda *_a, **_kw: snap)

    thread = threading.Thread(
        target=run_watchdog,
        kwargs={
            "task": task,
            "watched_pid": 1,
            "interval_seconds": 0.15,
            "max_cpu": None,
            "max_runtime_seconds": None,
            "max_rss_mib": None,
            "once": False,
            "dispatcher": dispatcher,
            "on_alarm": None,
        },
        daemon=True,
    )
    thread.start()
    time.sleep(0.05)
    task.request_cancel()
    thread.join(timeout=3.0)
    assert task.status == TaskStatus.CANCELLED


def test_run_watchdog_once_without_thresholds_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--once`` with no threshold flags must finish after one sample (Greptile #1969)."""
    from datetime import UTC, datetime, timedelta

    from platform.common.task_registry import TaskRegistry
    from tools.system.fleet_monitoring.probe import ProcessSnapshot
    from tools.system.watch_dog.monitor import run_watchdog

    reg = TaskRegistry()
    task = reg.create(TaskKind.WATCHDOG, command="watchdog pid=1")
    task.mark_running()
    dispatcher = MagicMock()
    dispatcher.dispatch = MagicMock(return_value=True)

    started_at = datetime.now(UTC) - timedelta(seconds=1)
    snap = ProcessSnapshot(
        pid=1,
        cpu_percent=1.0,
        rss_mb=10.0,
        num_fds=None,
        num_connections=None,
        status="running",
        started_at=started_at,
    )
    monkeypatch.setattr("tools.system.watch_dog.monitor.probe", lambda *_a, **_kw: snap)

    run_watchdog(
        task=task,
        watched_pid=1,
        interval_seconds=0.1,
        max_cpu=None,
        max_runtime_seconds=None,
        max_rss_mib=None,
        once=True,
        dispatcher=dispatcher,
        on_alarm=None,
    )
    assert task.status == TaskStatus.COMPLETED
    assert task.result == "single sample (once)"
    dispatcher.dispatch.assert_not_called()


def test_run_watchdog_first_probe_inaccessible_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live PID this user cannot introspect must fail loudly, not report
    'target process exited' (a permission failure is not an exit)."""
    from platform.common.task_registry import TaskRegistry
    from tools.system.watch_dog.monitor import run_watchdog

    reg = TaskRegistry()
    task = reg.create(TaskKind.WATCHDOG, command="watchdog pid=1")
    task.mark_running()
    dispatcher = MagicMock()
    dispatcher.dispatch = MagicMock(return_value=True)

    monkeypatch.setattr("tools.system.watch_dog.monitor.probe", lambda *_a, **_kw: None)
    monkeypatch.setattr("tools.system.watch_dog.monitor.pid_exists", lambda _pid: True)

    run_watchdog(
        task=task,
        watched_pid=1,
        interval_seconds=0.01,
        max_cpu=None,
        max_runtime_seconds=None,
        max_rss_mib=None,
        once=False,
        dispatcher=dispatcher,
        on_alarm=None,
    )
    assert task.status == TaskStatus.FAILED
    assert "permission denied" in (task.error or "")
    dispatcher.dispatch.assert_not_called()


def test_run_watchdog_gone_pid_completes_as_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.common.task_registry import TaskRegistry
    from tools.system.watch_dog.monitor import run_watchdog

    reg = TaskRegistry()
    task = reg.create(TaskKind.WATCHDOG, command="watchdog pid=1")
    task.mark_running()
    dispatcher = MagicMock()
    dispatcher.dispatch = MagicMock(return_value=True)

    monkeypatch.setattr("tools.system.watch_dog.monitor.probe", lambda *_a, **_kw: None)
    monkeypatch.setattr("tools.system.watch_dog.monitor.pid_exists", lambda _pid: False)

    run_watchdog(
        task=task,
        watched_pid=1,
        interval_seconds=0.01,
        max_cpu=None,
        max_runtime_seconds=None,
        max_rss_mib=None,
        once=False,
        dispatcher=dispatcher,
        on_alarm=None,
    )
    assert task.status == TaskStatus.COMPLETED
    assert task.result == "target process exited"


def test_run_watchdog_transient_inaccessible_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-watch AccessDenied tick (probe None while the PID exists) must
    retry, then still report the real exit when the PID is truly gone."""
    from datetime import UTC, datetime, timedelta

    from platform.common.task_registry import TaskRegistry
    from tools.system.fleet_monitoring.probe import ProcessSnapshot
    from tools.system.watch_dog.monitor import run_watchdog

    reg = TaskRegistry()
    task = reg.create(TaskKind.WATCHDOG, command="watchdog pid=1")
    task.mark_running()
    dispatcher = MagicMock()
    dispatcher.dispatch = MagicMock(return_value=True)

    snap = ProcessSnapshot(
        pid=1,
        cpu_percent=1.0,
        rss_mb=10.0,
        num_fds=None,
        num_connections=None,
        status="running",
        started_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    probe_results = [snap, None, None]
    probe_calls = {"n": 0}

    def _fake_probe(*_a: object, **_kw: object) -> ProcessSnapshot | None:
        probe_calls["n"] += 1
        return probe_results.pop(0)

    pid_exists_results = [True, False]
    monkeypatch.setattr("tools.system.watch_dog.monitor.probe", _fake_probe)
    monkeypatch.setattr(
        "tools.system.watch_dog.monitor.pid_exists",
        lambda _pid: pid_exists_results.pop(0),
    )

    run_watchdog(
        task=task,
        watched_pid=1,
        interval_seconds=0.01,
        max_cpu=None,
        max_runtime_seconds=None,
        max_rss_mib=None,
        once=False,
        dispatcher=dispatcher,
        on_alarm=None,
    )
    assert probe_calls["n"] == 3
    assert task.status == TaskStatus.COMPLETED
    assert task.result == "target process exited"
