"""Tests for the /loops slash command."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from platform.scheduler.loop_constants import LOOP_PROMPT_PARAM
from platform.scheduler.store import add_task, get_task, list_tasks
from platform.scheduler.types import Provider, ScheduledTask, TaskKind
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash, loops_cmds
from surfaces.interactive_shell.session import Session


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_slash_registry_includes_loops_command() -> None:
    assert "/loops" in SLASH_COMMANDS


def test_loops_lists_active_and_draft_schedules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.scheduler import store as scheduler_store

    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store_path)
    add_task(
        ScheduledTask(
            id="active-loop",
            name="Morning report",
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 8 * * 1-5",
            provider=Provider.SLACK,
        ),
        store_path,
    )
    add_task(
        ScheduledTask(
            id="draft-loop",
            name="PR sweep",
            kind=TaskKind.GITHUB_PR_SWEEP,
            cron="0 9 * * 1-5",
            provider=Provider.SLACK,
            enabled=False,
        ),
        store_path,
    )

    session = Session()
    console, buf = _capture()

    assert dispatch_slash("/loops", session, console) is True

    output = buf.getvalue()
    assert "Morning" in output
    assert "report" in output
    assert "PR sweep" in output
    assert "active" in output
    assert "draft" in output
    assert "0 8 * *" in output
    assert "UTC" in output
    assert "slack" in output


def test_loops_active_filters_drafts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.scheduler import store as scheduler_store

    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store_path)
    add_task(
        ScheduledTask(
            id="draft-loop",
            name="Draft PR sweep",
            kind=TaskKind.GITHUB_PR_SWEEP,
            cron="0 9 * * 1-5",
            provider=Provider.SLACK,
            enabled=False,
        ),
        store_path,
    )

    session = Session()
    console, buf = _capture()

    assert dispatch_slash("/loops active", session, console) is True

    output = buf.getvalue()
    assert "no active loops" in output
    assert "Draft PR sweep" not in output


def test_loops_add_creates_manual_loop_and_runs_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.scheduler import store as scheduler_store
    from platform.scheduler.store import list_tasks

    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store_path)
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_credentials",
        lambda _params: {},
    )
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_telegram_default_chat_id",
        lambda _params: "",
    )
    monkeypatch.setattr(
        "platform.scheduler.loops.resolve_slack_credentials",
        lambda _params: {},
    )
    run_calls: list[tuple[str, ...]] = []
    reload_calls: list[bool] = []

    def _fake_run_once(_console: Console, task_ids: tuple[str, ...]) -> bool:
        run_calls.append(task_ids)
        _console.print("fake run-now complete")
        return True

    def _fake_reload(_console: Console) -> None:
        reload_calls.append(True)
        _console.print("fake scheduler reload")

    monkeypatch.setattr(loops_cmds, "_run_loop_task_ids_once", _fake_run_once)
    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", _fake_reload)

    session = Session()
    console, buf = _capture()

    assert (
        dispatch_slash(
            '/loops add --name "Morning ops" --time 08:30 '
            '--prompt "Check open incidents and summarize risk" --run-now',
            session,
            console,
        )
        is True
    )

    tasks = list_tasks(store_path)
    assert len(tasks) == 1
    assert tasks[0].name == "Morning ops"
    assert tasks[0].cron == "30 8 * * *"
    assert tasks[0].provider == Provider.INTERACTIVE_SHELL
    assert tasks[0].params[LOOP_PROMPT_PARAM] == "Check open incidents and summarize risk"
    assert reload_calls == [True]
    assert run_calls == [(tasks[0].id,)]
    output = buf.getvalue()
    assert "loop created" in output
    assert "interactive shell" in output
    assert "fake scheduler reload" in output
    assert "fake run-now complete" in output


def test_loops_next_prints_schedule_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.scheduler import store as scheduler_store

    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store_path)
    task = add_task(
        ScheduledTask(
            id="manual-loop",
            name="Morning ops",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="30 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Check open incidents."},
        ),
        store_path,
    )

    session = Session()
    console, buf = _capture()

    assert dispatch_slash(f"/loops next {task.id}", session, console) is True

    output = buf.getvalue()
    assert "Morning ops" in output
    assert "30 8 * * *" in output
    assert "next run" in output
    assert "Check open incidents." in output


def test_loops_run_executes_resolved_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.scheduler import store as scheduler_store

    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store_path)
    add_task(
        ScheduledTask(
            id="manual-loop",
            name="Morning ops",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="30 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
        ),
        store_path,
    )
    run_calls: list[tuple[str, ...]] = []

    def _fake_run_once(_console: Console, task_ids: tuple[str, ...]) -> bool:
        run_calls.append(task_ids)
        return True

    monkeypatch.setattr(loops_cmds, "_run_loop_task_ids_once", _fake_run_once)

    session = Session()
    console, _buf = _capture()

    assert dispatch_slash("/loops run manual-loop", session, console) is True

    assert run_calls == [("manual-loop",)]


def test_loops_stop_disables_resolved_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.scheduler import store as scheduler_store

    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store_path)
    add_task(
        ScheduledTask(
            id="manual-loop",
            name="Morning ops",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="30 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Check open incidents."},
        ),
        store_path,
    )
    reload_calls: list[bool] = []

    def _fake_reload(_console: Console) -> None:
        reload_calls.append(True)

    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", _fake_reload)

    session = Session()
    console, buf = _capture()

    assert dispatch_slash("/loops stop manual-loop", session, console) is True

    task = get_task("manual-loop", store_path)
    assert task is not None
    assert task.enabled is False
    assert reload_calls == [True]
    assert "loop stopped" in buf.getvalue()


def test_loops_start_enables_resolved_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.scheduler import store as scheduler_store

    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store_path)
    add_task(
        ScheduledTask(
            id="manual-loop",
            name="Morning ops",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="30 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            enabled=False,
            params={LOOP_PROMPT_PARAM: "Check open incidents."},
        ),
        store_path,
    )
    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", lambda _console: None)

    session = Session()
    console, buf = _capture()

    assert dispatch_slash("/loops start manual-loop", session, console) is True

    task = get_task("manual-loop", store_path)
    assert task is not None
    assert task.enabled is True
    assert task.next_run is not None
    assert "loop started" in buf.getvalue()


def test_loops_delete_removes_resolved_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.scheduler import store as scheduler_store

    store_path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(scheduler_store, "_default_store_path", lambda: store_path)
    add_task(
        ScheduledTask(
            id="manual-loop",
            name="Morning ops",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="30 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Check open incidents."},
        ),
        store_path,
    )
    reload_calls: list[bool] = []

    def _fake_reload(_console: Console) -> None:
        reload_calls.append(True)

    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", _fake_reload)

    session = Session()
    console, buf = _capture()

    assert dispatch_slash("/loops delete manual-loop", session, console) is True

    assert list_tasks(store_path) == []
    assert reload_calls == [True]
    assert "loop deleted" in buf.getvalue()


def test_loops_messages_lists_local_inbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform.scheduler import local_delivery
    from platform.scheduler.local_delivery import record_loop_message

    inbox_path = tmp_path / "loop_messages.jsonl"
    monkeypatch.setattr(local_delivery, "_default_inbox_path", lambda: inbox_path)
    record_loop_message(
        ScheduledTask(
            id="manual-loop",
            name="Morning ops",
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="30 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
        ),
        "Manual loop report",
    )

    session = Session()
    console, buf = _capture()

    assert dispatch_slash("/loops messages", session, console) is True

    output = buf.getvalue()
    assert "Morning ops" in output
    assert "Manual loop report" in output
