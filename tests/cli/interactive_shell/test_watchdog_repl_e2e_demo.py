"""End-to-end style demo for REPL watchdog slash commands.

This file is the reviewer-facing "proper e2e demo": it drives the same
``dispatch_slash`` path as the live REPL (trust, /watch, /watches, /tasks,
/unwatch) with a real background watchdog thread and a stubbed ``probe`` so
CI stays deterministic and offline.

For the GitHub **Demo/Screenshot** box, paste the steps from ``docs/DEVELOPMENT.md``
(**Interactive shell: REPL watchdog demo**) or ``repl_watchdog_demo.md`` in this directory.

Run only this module::

    uv run pytest tests/cli/interactive_shell/test_watchdog_repl_e2e_demo.py -v --tb=short
"""

from __future__ import annotations

import io
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from rich.console import Console

from app.agents.probe import ProcessSnapshot
from app.cli.interactive_shell.commands import dispatch_slash
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.tasks import TaskKind, TaskStatus
from app.watch_dog.alarms import AlarmCredentials


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_repl_watchdog_end_to_end_demo_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the full REPL slash pipeline for watchdogs (deterministic probe stub)."""
    monkeypatch.setattr(
        "app.cli.interactive_shell.command_registry.watch_cmds.load_credentials_from_env",
        lambda *_a, **_kw: AlarmCredentials(bot_token="demo-token", chat_id="1"),
    )
    monkeypatch.setattr(
        "app.cli.interactive_shell.command_registry.watch_cmds.pid_exists",
        lambda _pid: True,
    )

    pid = os.getpid()
    started_at = datetime.now(UTC) - timedelta(seconds=30)
    snap = ProcessSnapshot(
        pid=pid,
        cpu_percent=5.0,
        rss_mb=64.0,
        num_fds=None,
        num_connections=None,
        status="running",
        started_at=started_at,
    )
    monkeypatch.setattr("app.watch_dog.monitor.probe", lambda *_a, **_kw: snap)

    session = ReplSession()
    console, buf = _capture()

    assert dispatch_slash("/trust on", session, console, is_tty=True) is True

    assert (
        dispatch_slash(
            f"/watch {pid} --max-cpu 80 --interval 0.15",
            session,
            console,
            is_tty=True,
        )
        is True
    )
    assert "started" in buf.getvalue()

    task = next(t for t in session.task_registry.list_recent(50) if t.kind == TaskKind.WATCHDOG)
    task_id = task.task_id

    for _ in range(40):
        if task.progress and "cpu=" in (task.progress or ""):
            break
        time.sleep(0.05)
    assert task.progress, "watchdog thread should publish at least one sample line"

    buf.truncate(0)
    buf.seek(0)
    assert dispatch_slash("/watches", session, console, is_tty=True) is True
    watches_out = buf.getvalue()
    assert task_id in watches_out
    assert "Watchdogs" in watches_out
    assert "running" in watches_out.lower()
    assert "watchdog" in watches_out.lower()

    buf.truncate(0)
    buf.seek(0)
    assert dispatch_slash("/tasks", session, console, is_tty=True) is True
    tasks_out = buf.getvalue()
    assert task_id in tasks_out
    assert "watchdog" in tasks_out.lower()

    assert dispatch_slash(f"/unwatch {task_id}", session, console, is_tty=True) is True
    for _ in range(80):
        if task.status == TaskStatus.CANCELLED:
            break
        time.sleep(0.05)
    assert task.status == TaskStatus.CANCELLED

    buf.truncate(0)
    buf.seek(0)
    assert dispatch_slash("/watches", session, console, is_tty=True) is True
    assert "cancelled" in buf.getvalue().lower()
