"""Synthetic test task runner — watch subprocess lifecycle and report outcomes."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.error_handling.exception_reporting import report_exception
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    evaluate_synthetic_test_launch,
    execution_allowed,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
    DEFAULT_SYNTHETIC_SCENARIO,
    SYNTHETIC_UNKNOWN_PREFIX,
    list_rds_postgres_scenarios,
)
from app.cli.interactive_shell.runtime import ReplSession, TaskKind, TaskRecord
from app.cli.interactive_shell.ui import DIM, ERROR, HIGHLIGHT

from .task_streaming import (
    _SYNTHETIC_DIAG_CHARS,
    _SYNTHETIC_POLL_SECONDS,
    SYNTHETIC_TEST_TIMEOUT_SECONDS,
    _join_task_output_streams,
    _start_task_output_streams,
    _subprocess_env_with_aligned_width,
    read_diag,
    terminate_child_process,
)

_SYNTHETIC_SCENARIO_ID_RE = re.compile(r"^\d{3}-[a-z0-9][a-z0-9-]*$")


def watch_synthetic_subprocess(
    task: TaskRecord,
    proc: subprocess.Popen[Any],
    session: ReplSession,
    suite_name: str,
    stderr_buf: tempfile.SpooledTemporaryFile[bytes],  # type: ignore[type-arg]
    console: Console | None = None,
) -> None:
    def _history_text() -> str:
        return f"{suite_name} task:{task.task_id}"

    history_gen_when_watch_started = session.history_generation

    def _record_synthetic_if_current_session(ok: bool) -> None:
        if session.history_generation != history_gen_when_watch_started:
            return
        session.record("synthetic_test", _history_text(), ok=ok)

    def _run() -> None:
        output_threads: list[threading.Thread] = []
        suggest_follow_up = False
        try:
            output_threads = (
                _start_task_output_streams(
                    task=task,
                    proc=proc,
                    console=console,
                    stderr_capture=stderr_buf,
                )
                if console is not None
                else []
            )
            started = time.monotonic()
            timed_out = False
            # Track whether *we* explicitly terminated the process so we can
            # distinguish a cancel-driven exit from a natural exit that happened
            # to race with a concurrent /cancel.
            terminated_by_watcher = False
            while proc.poll() is None:
                if time.monotonic() - started > SYNTHETIC_TEST_TIMEOUT_SECONDS:
                    timed_out = True
                    task.request_cancel()
                    terminate_child_process(proc)
                    terminated_by_watcher = True
                    break
                if task.cancel_requested.is_set():
                    terminate_child_process(proc)
                    terminated_by_watcher = True
                    break
                time.sleep(_SYNTHETIC_POLL_SECONDS)

            if timed_out:
                task.mark_failed(f"timed out after {SYNTHETIC_TEST_TIMEOUT_SECONDS}s")
                _record_synthetic_if_current_session(ok=False)
                suggest_follow_up = True
                return

            _join_task_output_streams(output_threads)
            code = proc.returncode
            if code is None:
                task.mark_failed("subprocess did not report exit code")
                _record_synthetic_if_current_session(ok=False)
                suggest_follow_up = True
                return

            # Honour the real exit code when the process exited on its own.
            # Only treat as CANCELLED when *we* killed it after a cancel request;
            # a natural exit that races with /cancel should be recorded by its code.
            if terminated_by_watcher and task.cancel_requested.is_set():
                task.mark_cancelled()
                _record_synthetic_if_current_session(ok=False)
                return

            if code == 0:
                task.mark_completed(result="ok")
                _record_synthetic_if_current_session(ok=True)
            else:
                diag = read_diag(stderr_buf)
                error_msg = f"exit code {code}" + (f": {diag}" if diag else "")
                task.mark_failed(error_msg)
                _record_synthetic_if_current_session(ok=False)
                suggest_follow_up = True
        except Exception as exc:  # noqa: BLE001
            task.mark_failed(str(exc))
            report_exception(exc, context="interactive_shell.synthetic_test.watch")
            _record_synthetic_if_current_session(ok=False)
            suggest_follow_up = True
            if console is not None:
                console.print(f"[{ERROR}]synthetic watcher failed:[/] {escape(str(exc))}")
        finally:
            _join_task_output_streams(output_threads)
            stderr_buf.close()
            if suggest_follow_up and session.history_generation == history_gen_when_watch_started:
                session.suggest_synthetic_failure_follow_up(label=suite_name)
            else:
                session.notify_prompt_changed()

    threading.Thread(target=_run, daemon=True, name=f"synthetic-{task.task_id}").start()


def run_synthetic_test(
    suite_name: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    suite_spec = suite_name.strip().lower()

    # The planner emits this sentinel when the user explicitly named a numeric
    # scenario ID that isn't in the on-disk suite (e.g. "test 016" when only
    # 000-015 exist). Surface the error before any execution-policy / subprocess
    # work so we never silently launch the default scenario in its place.
    if suite_spec.startswith(SYNTHETIC_UNKNOWN_PREFIX):
        hint = suite_spec[len(SYNTHETIC_UNKNOWN_PREFIX) :]
        console.print(f"[{ERROR}]no synthetic scenario matches[/] '{escape(hint)}'.")
        available = list_rds_postgres_scenarios()
        if available:
            console.print(f"Available scenarios ({len(available)}):")
            for name in available:
                console.print(f"  • {name}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    resolved_suite_name = ""
    resolved_scenario = DEFAULT_SYNTHETIC_SCENARIO
    run_all = False
    if suite_spec == "rds_postgres":
        resolved_suite_name = "rds_postgres"
    elif suite_spec == "rds_postgres:all":
        resolved_suite_name = "rds_postgres"
        run_all = True
    elif suite_spec.startswith("rds_postgres:"):
        requested_scenario = suite_spec.split(":", 1)[1].strip()
        if requested_scenario and _SYNTHETIC_SCENARIO_ID_RE.fullmatch(requested_scenario):
            resolved_suite_name = "rds_postgres"
            resolved_scenario = requested_scenario
    if resolved_suite_name != "rds_postgres":
        console.print(f"[{ERROR}]unknown synthetic suite:[/] {escape(suite_name)}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    policy = evaluate_synthetic_test_launch()
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary=(
            "opensre tests synthetic all"
            if run_all
            else f"opensre tests synthetic --scenario {resolved_scenario}"
        ),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("synthetic_test", suite_name, ok=False)
        return

    display_command = (
        "opensre tests synthetic all"
        if run_all
        else f"opensre tests synthetic --scenario {resolved_scenario}"
    )
    console.print(f"[bold]$ {display_command}[/bold]")
    session.last_synthetic_observation_path = None
    task = session.task_registry.create(TaskKind.SYNTHETIC_TEST, command=display_command)
    task.mark_running()
    # Lifetime managed by the watcher thread's finally block; SIM115 ignored
    # for this file in ruff.toml.
    stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg]
        max_size=_SYNTHETIC_DIAG_CHARS * 2
    )
    try:
        proc = subprocess.Popen(
            (
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "app.cli",
                    "tests",
                    "synthetic",
                    "all",
                ]
                if run_all
                else [
                    sys.executable,
                    "-u",
                    "-m",
                    "app.cli",
                    "tests",
                    "synthetic",
                    "--scenario",
                    resolved_scenario,
                ]
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
            env=_subprocess_env_with_aligned_width(console),
        )
    except Exception as exc:
        stderr_buf.close()
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.synthetic_test.start")
        console.print(f"[{ERROR}]synthetic test failed to start:[/] {escape(str(exc))}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    # Record the initial entry BEFORE starting the watcher so that
    # watch_synthetic_subprocess captures the updated history_generation
    # in its guard. Without this ordering the watcher's
    # _record_synthetic_if_current_session would see the generation
    # increment and incorrectly skip recording the completion result.
    session.record("synthetic_test", suite_name)
    task.attach_process(proc)
    watch_synthetic_subprocess(
        task,
        proc,
        session,
        f"{resolved_suite_name}:{resolved_scenario}",
        stderr_buf,
        console,
    )
    console.print(
        f"[{DIM}]synthetic test started — task[/] [bold]{escape(task.task_id)}[/bold]. "
        f"[{HIGHLIGHT}]/tasks[/] [{DIM}]to monitor,[/] "
        f"[{HIGHLIGHT}]/cancel {escape(task.task_id)}[/] [{DIM}]to stop.[/]"
    )
