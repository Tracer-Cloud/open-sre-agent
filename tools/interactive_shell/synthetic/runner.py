"""Synthetic test task runner — watch subprocess lifecycle and report outcomes."""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from typing import Any

from platform.common.task_types import TaskKind, TaskRecord
from tools.interactive_shell.cli import build_opensre_cli_argv
from tools.interactive_shell.shared import allow_tool
from tools.interactive_shell.subprocess import (
    SYNTHETIC_DIAG_CHARS,
    SYNTHETIC_TEST_TIMEOUT_SECONDS,
    SubprocessPresenter,
    read_diag,
    watch_subprocess_until_exit,
)

DEFAULT_SYNTHETIC_SCENARIO = "001-replication-lag"

_SYNTHETIC_SCENARIO_ID_RE = re.compile(r"^\d{3}-[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class SyntheticSuiteSpec:
    """Resolved synthetic suite/scenario selection."""

    suite_name: str
    scenario: str
    run_all: bool
    display_command: str
    valid: bool


def resolve_synthetic_suite(suite_name: str) -> SyntheticSuiteSpec:
    suite_spec = suite_name.strip().lower()
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
    display_command = (
        "opensre tests synthetic all"
        if run_all
        else f"opensre tests synthetic --scenario {resolved_scenario}"
    )
    return SyntheticSuiteSpec(
        suite_name=resolved_suite_name,
        scenario=resolved_scenario,
        run_all=run_all,
        display_command=display_command,
        valid=resolved_suite_name == "rds_postgres",
    )


def synthetic_cli_argv(spec: SyntheticSuiteSpec) -> list[str]:
    argv = (
        build_opensre_cli_argv(["tests", "synthetic", "all"])
        if spec.run_all
        else build_opensre_cli_argv(
            ["tests", "synthetic", "--scenario", spec.scenario],
        )
    )
    if not argv:
        return argv
    return [argv[0], "-u", *argv[1:]]


def spawn_synthetic_subprocess(
    spec: SyntheticSuiteSpec,
    *,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        synthetic_cli_argv(spec),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
        env=env,
    )


def watch_synthetic_subprocess(
    task: TaskRecord,
    proc: subprocess.Popen[Any],
    presenter: SubprocessPresenter,
    suite_name: str,
    stderr_buf: tempfile.SpooledTemporaryFile[bytes],  # type: ignore[type-arg]
) -> None:
    session = presenter.session

    def _history_text() -> str:
        return f"{suite_name} task:{task.task_id}"

    history_gen_when_watch_started = session.terminal.history_generation

    def _record_synthetic_if_current_session(ok: bool) -> None:
        if session.terminal.history_generation != history_gen_when_watch_started:
            return
        session.record("synthetic_test", _history_text(), ok=ok)

    def _run() -> None:
        output_threads: list[threading.Thread] = []
        suggest_follow_up = False
        try:
            output_threads = presenter.start_task_output_streams(
                task=task,
                proc=proc,
                stderr_capture=stderr_buf,
            )
            watch_result = watch_subprocess_until_exit(
                proc,
                cancel_event=task.cancel_requested,
                timeout_seconds=SYNTHETIC_TEST_TIMEOUT_SECONDS,
            )

            if watch_result.timed_out:
                task.mark_failed(f"timed out after {SYNTHETIC_TEST_TIMEOUT_SECONDS}s")
                _record_synthetic_if_current_session(ok=False)
                suggest_follow_up = True
                return

            presenter.join_task_output_streams(output_threads)
            code = watch_result.exit_code
            if code is None:
                task.mark_failed("subprocess did not report exit code")
                _record_synthetic_if_current_session(ok=False)
                suggest_follow_up = True
                return

            if watch_result.terminated_by_watcher and watch_result.cancelled:
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
            presenter.report_exception(
                exc, context="surfaces.interactive_shell.synthetic_test.watch"
            )
            _record_synthetic_if_current_session(ok=False)
            suggest_follow_up = True
            presenter.print_error(f"synthetic watcher failed: {exc}")
        finally:
            presenter.join_task_output_streams(output_threads)
            stderr_buf.close()
            if (
                suggest_follow_up
                and session.terminal.history_generation == history_gen_when_watch_started
            ):
                session.suggest_synthetic_failure_follow_up(label=suite_name)
            else:
                session.terminal.notify_prompt_changed()

    threading.Thread(target=_run, daemon=True, name=f"synthetic-{task.task_id}").start()


def run_synthetic_test(suite_name: str, presenter: SubprocessPresenter) -> None:
    session = presenter.session
    spec = resolve_synthetic_suite(suite_name)
    if not spec.valid:
        presenter.print_error(f"unknown synthetic suite: {suite_name}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    policy = allow_tool("synthetic_test")
    if not presenter.execution_allowed(
        policy,
        action_summary=spec.display_command,
    ):
        session.record("synthetic_test", suite_name, ok=False)
        return

    presenter.print_bold_command(spec.display_command)
    session.last_synthetic_observation_path = None
    task = session.task_registry.create(TaskKind.SYNTHETIC_TEST, command=spec.display_command)
    task.mark_running()
    stderr_buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(  # type: ignore[type-arg]
        max_size=SYNTHETIC_DIAG_CHARS * 2
    )
    try:
        proc = spawn_synthetic_subprocess(spec, env=presenter.subprocess_env())
    except Exception as exc:
        stderr_buf.close()
        task.mark_failed(str(exc))
        presenter.report_exception(exc, context="surfaces.interactive_shell.synthetic_test.start")
        presenter.print_error(f"synthetic test failed to start: {exc}")
        session.record("synthetic_test", suite_name, ok=False)
        return

    session.record("synthetic_test", suite_name)
    task.attach_process(proc)
    watch_synthetic_subprocess(
        task,
        proc,
        presenter,
        f"{spec.suite_name}:{spec.scenario}",
        stderr_buf,
    )
    presenter.print(
        f"[dim]synthetic test started — task[/] [bold]{task.task_id}[/bold]. "
        "[highlight]/tasks[/] [dim]to monitor,[/] "
        f"[highlight]/cancel {task.task_id}[/] [dim]to stop.[/]"
    )


__all__ = ["run_synthetic_test", "watch_synthetic_subprocess"]
