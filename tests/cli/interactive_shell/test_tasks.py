"""Tests for REPL task registry and /tasks · /cancel."""

from __future__ import annotations

import io
import tempfile
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from app.cli.interactive_shell.commands import dispatch_slash
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.tasks import TaskKind, TaskRegistry, TaskStatus


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


class TestTaskRecord:
    def test_lifecycle_completed(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.INVESTIGATION)
        assert t.status == TaskStatus.PENDING
        t.mark_running()
        assert t.status == TaskStatus.RUNNING
        t.mark_completed(result="done")
        assert t.status == TaskStatus.COMPLETED
        assert t.result == "done"
        assert t.ended_at is not None
        t.mark_failed("x")
        assert t.status == TaskStatus.COMPLETED

    def test_mark_cancelled_idempotent_after_terminal(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.INVESTIGATION)
        t.mark_running()
        t.mark_cancelled()
        assert t.status == TaskStatus.CANCELLED
        t.mark_completed(result="nope")
        assert t.status == TaskStatus.CANCELLED

    def test_request_cancel_sets_event_even_when_pending(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.INVESTIGATION)
        assert t.request_cancel() is False
        assert t.cancel_requested.is_set()
        assert t.status == TaskStatus.PENDING

    def test_request_cancel_sets_event_and_terminates_process(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.SYNTHETIC_TEST)
        t.mark_running()
        proc = MagicMock()
        proc.poll.return_value = None
        t.attach_process(proc)
        assert t.request_cancel() is True
        proc.terminate.assert_called_once()
        assert t.cancel_requested.is_set()


class TestTaskRegistry:
    def test_get_single_prefix_match(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.INVESTIGATION)
        assert reg.get(t.task_id[:4]) == t
        assert reg.get("") is None

    def test_candidates_ambiguous_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _ids = iter(["11111111", "11112222"])

        def _fake_hex(_nbytes: int) -> str:
            return next(_ids)

        monkeypatch.setattr("app.cli.interactive_shell.tasks.secrets.token_hex", _fake_hex)
        session = ReplSession()
        session.task_registry.create(TaskKind.INVESTIGATION)
        session.task_registry.create(TaskKind.INVESTIGATION)
        console, buf = _capture()
        dispatch_slash("/cancel 1111", session, console)
        assert "ambiguous" in buf.getvalue().lower()

    def test_ring_buffer_drops_oldest(self) -> None:
        reg = TaskRegistry(max_tasks=3)
        first = reg.create(TaskKind.INVESTIGATION)
        reg.create(TaskKind.INVESTIGATION)
        reg.create(TaskKind.INVESTIGATION)
        reg.create(TaskKind.INVESTIGATION)
        recent_ids = [t.task_id for t in reg.list_recent(10)]
        assert first.task_id not in recent_ids
        assert len(recent_ids) == 3


class TestSlashTaskCommands:
    def test_tasks_empty_message(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        dispatch_slash("/tasks", session, console)
        assert "no tasks" in buf.getvalue().lower()

    def test_tasks_shows_recent_rows(self) -> None:
        session = ReplSession()
        t = session.task_registry.create(TaskKind.INVESTIGATION)
        t.mark_running()
        t.mark_completed(result="rc")
        console, buf = _capture()
        dispatch_slash("/tasks", session, console)
        out = buf.getvalue()
        assert t.task_id in out
        assert "investigation" in out
        assert "completed" in out

    def test_cancel_usage_without_id(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        dispatch_slash("/cancel", session, console)
        assert "usage" in buf.getvalue().lower()

    def test_cancel_unknown_id(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        dispatch_slash("/cancel deadbeef", session, console)
        assert "no task" in buf.getvalue().lower()

    def test_cancel_completed_task_message(self) -> None:
        session = ReplSession()
        t = session.task_registry.create(TaskKind.INVESTIGATION)
        t.mark_running()
        t.mark_completed(result="x")
        console, buf = _capture()
        dispatch_slash(f"/cancel {t.task_id}", session, console)
        assert "already finished" in buf.getvalue().lower()

    def test_cancel_running_investigation_signals(self) -> None:
        session = ReplSession()
        t = session.task_registry.create(TaskKind.INVESTIGATION)
        t.mark_running()
        console, buf = _capture()
        dispatch_slash(f"/cancel {t.task_id}", session, console)
        out = buf.getvalue()
        assert "cancellation" in out.lower()
        assert "Ctrl+C" in out


class _ImmediateThread:
    """Run ``target`` synchronously inside ``start()`` (for deterministic tests)."""

    def __init__(
        self,
        group: object = None,
        target: Callable[[], None] | None = None,
        name: object = None,
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        *,
        daemon: object = None,
    ) -> None:  # noqa: ARG002 - mimic threading.Thread ctor
        del group, args, kwargs, daemon, name  # threaded API baggage
        if target is None:
            raise TypeError("target required")
        self._target = target

    def start(self) -> None:
        self._target()


class _DeferredSyntheticThread:
    """Queue ``target`` via ``start()``; tests invoke queued callables explicitly."""

    pending: list[Callable[[], None]] = []

    def __init__(
        self,
        group: object = None,
        target: Callable[[], None] | None = None,
        name: object = None,
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        *,
        daemon: object = None,
    ) -> None:  # noqa: ARG002 - mimic threading.Thread ctor
        del group, args, kwargs, daemon, name
        if target is None:
            raise TypeError("target required")
        self._target = target

    def start(self) -> None:
        _DeferredSyntheticThread.pending.append(self._target)


class TestSyntheticSubprocessWatcher:
    def test_watch_marks_completed_when_process_already_done(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.cli.interactive_shell.action_executor as ae

        monkeypatch.setattr(ae.threading, "Thread", _ImmediateThread)

        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0

        session = ReplSession()
        task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
        task.mark_running()
        task.attach_process(proc)
        stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile()  # type: ignore[type-arg]  # noqa: SIM115
        ae.watch_synthetic_subprocess(task, proc, session, "rds_postgres", stderr_buf)
        assert task.status == TaskStatus.COMPLETED
        hist = session.history[-1]
        assert hist["type"] == "synthetic_test"
        assert hist["ok"] is True
        assert "task:" in hist["text"]

    def test_watch_honours_exit_code_when_cancel_races_loop_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Process exits naturally (code 0) in the same poll tick that /cancel fires.

        The poll loop exits because proc.poll() returns non-None *before* the
        cancel_requested branch runs, so terminated_by_watcher stays False.
        The task must be COMPLETED, not CANCELLED — the process succeeded.
        """
        import app.cli.interactive_shell.action_executor as ae

        monkeypatch.setattr(ae.threading, "Thread", _ImmediateThread)

        session = ReplSession()
        task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
        task.mark_running()
        proc = MagicMock()

        # poll() returns None once (enter loop body), then cancel fires via
        # sleep, which also makes poll return 0 — loop exits via while condition.
        pending: list[int | None] = [None]

        def _poll_side() -> int | None:
            return pending[0]

        proc.poll.side_effect = _poll_side
        proc.returncode = 0

        sleeps: list[float] = []

        def _fake_sleep(_secs: float) -> None:
            sleeps.append(_secs)
            task.cancel_requested.set()
            pending[0] = 0  # process finishes naturally in the same window

        monkeypatch.setattr(ae.time, "sleep", _fake_sleep)
        stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile()  # type: ignore[type-arg]  # noqa: SIM115
        ae.watch_synthetic_subprocess(task, proc, session, "rds_postgres", stderr_buf)
        # terminated_by_watcher is False → honour exit code 0 → COMPLETED
        assert task.status == TaskStatus.COMPLETED
        assert sleeps

    def test_watch_marks_cancelled_when_watcher_kills_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cancel_requested is set while proc is still running; watcher terminates it."""
        import app.cli.interactive_shell.action_executor as ae

        monkeypatch.setattr(ae.threading, "Thread", _ImmediateThread)

        session = ReplSession()
        task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
        task.mark_running()
        proc = MagicMock()

        # poll() always returns None so the watcher's cancel branch runs and
        # calls _terminate_child_process; returncode is set after that.
        proc.poll.return_value = None
        proc.returncode = -15

        task.cancel_requested.set()  # cancel already set before first loop check

        # Skip the sleep so the loop iterates immediately to the cancel branch.
        monkeypatch.setattr(ae.time, "sleep", lambda _: None)

        stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile()  # type: ignore[type-arg]  # noqa: SIM115
        ae.watch_synthetic_subprocess(task, proc, session, "rds_postgres", stderr_buf)
        assert task.status == TaskStatus.CANCELLED
        hist = session.history[-1]
        assert hist["type"] == "synthetic_test"
        assert hist["ok"] is False

    def test_watch_honours_exit_code_when_cancel_races_natural_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Process exits naturally (code 0) while /cancel fires concurrently.

        The watcher should mark the task COMPLETED, not CANCELLED, because we
        never called _terminate_child_process — the process was already gone.
        """
        import app.cli.interactive_shell.action_executor as ae

        monkeypatch.setattr(ae.threading, "Thread", _ImmediateThread)

        session = ReplSession()
        task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
        task.mark_running()
        proc = MagicMock()
        # Process already finished; poll returns non-None immediately so the
        # while-loop body never executes — terminated_by_watcher stays False.
        proc.poll.return_value = 0
        proc.returncode = 0

        # Simulate /cancel arriving just as the watcher reads poll()
        task.cancel_requested.set()

        stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile()  # type: ignore[type-arg]  # noqa: SIM115
        ae.watch_synthetic_subprocess(task, proc, session, "rds_postgres", stderr_buf)
        assert task.status == TaskStatus.COMPLETED

    def test_watch_captures_stderr_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Diagnostic stderr output is included in mark_failed message."""
        import app.cli.interactive_shell.action_executor as ae

        monkeypatch.setattr(ae.threading, "Thread", _ImmediateThread)

        session = ReplSession()
        task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
        task.mark_running()
        proc = MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1

        stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile()  # type: ignore[type-arg]  # noqa: SIM115
        stderr_buf.write(b"ConnectionError: database unreachable\n")
        ae.watch_synthetic_subprocess(task, proc, session, "rds_postgres", stderr_buf)
        assert task.status == TaskStatus.FAILED
        assert "exit code 1" in (task.error or "")
        assert "ConnectionError" in (task.error or "")

    def test_watch_skips_synthetic_history_after_reset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.cli.interactive_shell.action_executor as ae

        _DeferredSyntheticThread.pending.clear()
        monkeypatch.setattr(ae.threading, "Thread", _DeferredSyntheticThread)

        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0

        session = ReplSession()
        task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
        task.mark_running()
        task.attach_process(proc)
        stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile()  # type: ignore[type-arg]  # noqa: SIM115
        ae.watch_synthetic_subprocess(task, proc, session, "rds_postgres", stderr_buf)
        assert len(_DeferredSyntheticThread.pending) == 1
        session.clear()
        _DeferredSyntheticThread.pending[0]()
        assert session.history == []
        _DeferredSyntheticThread.pending.clear()

    def test_deferred_watcher_writes_history_when_no_reset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.cli.interactive_shell.action_executor as ae

        _DeferredSyntheticThread.pending.clear()
        monkeypatch.setattr(ae.threading, "Thread", _DeferredSyntheticThread)

        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0

        session = ReplSession()
        task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
        task.mark_running()
        task.attach_process(proc)
        stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile()  # type: ignore[type-arg]  # noqa: SIM115
        ae.watch_synthetic_subprocess(task, proc, session, "rds_postgres", stderr_buf)
        _DeferredSyntheticThread.pending[0]()
        assert session.history[-1]["type"] == "synthetic_test"
        _DeferredSyntheticThread.pending.clear()
