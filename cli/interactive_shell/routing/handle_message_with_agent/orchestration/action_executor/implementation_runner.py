"""Claude Code implementation runner."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from cli.interactive_shell.error_handling.exception_reporting import report_exception
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    evaluate_code_agent_launch,
    execution_allowed,
)
from cli.interactive_shell.runtime import ReplSession, TaskKind
from cli.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING, print_command_output
from integrations.llm_cli.claude_code import ClaudeCodeAdapter
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env

from .task_streaming import (
    _MAX_COMMAND_OUTPUT_CHARS,
    _SYNTHETIC_DIAG_CHARS,
    CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS,
    terminate_child_process,
)

_ACTION_EXECUTOR_MODULE = (
    "cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor"
)


def _get_claude_code_adapter_cls() -> type[Any]:
    """Look up ClaudeCodeAdapter via the action_executor package namespace.

    This indirection lets tests monkeypatch ``action_executor.ClaudeCodeAdapter``
    and have the patch take effect even though the implementation lives in a submodule.
    Falls back to the directly-imported class when the package is not yet loaded
    (e.g. in isolated unit tests for this submodule).
    """
    ae = sys.modules.get(_ACTION_EXECUTOR_MODULE)
    cls = getattr(ae, "ClaudeCodeAdapter", None) if ae is not None else None
    return cls if cls is not None else ClaudeCodeAdapter


_IMPLEMENT_PERMISSION_MODE_ENV = "CLAUDE_CODE_IMPLEMENT_PERMISSION_MODE"
_DEFAULT_IMPLEMENT_PERMISSION_MODE = "acceptEdits"


def _recent_cli_agent_context(session: ReplSession, *, limit: int = 6) -> str:
    recent = session.cli_agent_messages[-limit:]
    if not recent:
        return ""
    return "\n".join(f"{role}: {text}" for role, text in recent)


def _is_context_dependent_implementation_request(request: str) -> bool:
    normalized = " ".join(request.strip().lower().split())
    return normalized in {
        "implement",
        "please implement",
        "code",
        "make the change",
        "make those changes",
    }


def _build_claude_code_implementation_prompt(request: str, session: ReplSession) -> str:
    context = _recent_cli_agent_context(session)
    context_block = (
        f"--- Recent OpenSRE terminal assistant context ---\n{context}\n\n" if context else ""
    )
    return (
        "You are Claude Code working in the current OpenSRE repository.\n\n"
        f"{context_block}"
        f"--- User implementation request ---\n{request.strip()}\n\n"
        "--- Rules ---\n"
        "- Implement the requested change in this repository.\n"
        "- Follow AGENTS.md, existing project conventions, and local code style.\n"
        "- Do not create a git commit or push changes.\n"
        "- Do not run destructive git commands such as reset --hard or checkout --.\n"
        "- Preserve unrelated user changes in the working tree.\n"
        "- Run focused tests or lint checks when practical.\n"
        "- Finish with a concise summary of changed files and verification performed.\n"
    )


def _implementation_argv(argv: tuple[str, ...]) -> list[str]:
    exec_argv = list(argv)
    permission_mode = os.environ.get(
        _IMPLEMENT_PERMISSION_MODE_ENV,
        _DEFAULT_IMPLEMENT_PERMISSION_MODE,
    ).strip()
    if permission_mode and permission_mode.lower() not in {"default", "none", "off"}:
        exec_argv.extend(["--permission-mode", permission_mode])
    return exec_argv


def run_claude_code_implementation(
    request: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    action_already_listed: bool = False,
) -> None:
    policy = evaluate_code_agent_launch()
    if not execution_allowed(
        policy,
        session=session,
        console=console,
        action_summary=f"Claude Code implementation: {request}",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=action_already_listed,
    ):
        session.record("implementation", request, ok=False)
        return

    if _is_context_dependent_implementation_request(request) and not session.cli_agent_messages:
        console.print(
            f"[{ERROR}]implementation request is too vague:[/] "
            "describe what Claude Code should change."
        )
        session.record("implementation", request, ok=False)
        return

    adapter = _get_claude_code_adapter_cls()()
    probe = adapter.detect()
    if not probe.installed or not probe.bin_path:
        console.print(f"[{ERROR}]Claude Code CLI not available:[/] {escape(probe.detail)}")
        session.record("implementation", request, ok=False)
        return
    if probe.logged_in is False:
        console.print(f"[{ERROR}]Claude Code is not authenticated:[/] {escape(probe.detail)}")
        session.record("implementation", request, ok=False)
        return

    prompt = _build_claude_code_implementation_prompt(request, session)
    try:
        invocation = adapter.build(
            prompt=prompt,
            model=os.environ.get("CLAUDE_CODE_MODEL"),
            workspace=str(Path.cwd()),
        )
    except Exception as exc:
        report_exception(exc, context="interactive_shell.claude_code.build")
        console.print(f"[{ERROR}]Claude Code failed to prepare:[/] {escape(str(exc))}")
        session.record("implementation", request, ok=False)
        return

    display_command = "claude -p"
    console.print(f"[bold]$ {display_command}[/bold]")
    task = session.task_registry.create(TaskKind.CODE_AGENT, command=display_command)
    task.mark_running()
    history_gen_when_started = session.history_generation

    try:
        proc = subprocess.Popen(
            _implementation_argv(invocation.argv),
            stdin=subprocess.PIPE if invocation.stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=invocation.cwd,
            env=build_cli_subprocess_env(invocation.env),
            start_new_session=True,
        )
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.claude_code.start")
        console.print(f"[{ERROR}]Claude Code failed to start:[/] {escape(str(exc))}")
        session.record("implementation", request, ok=False)
        return

    task.attach_process(proc)
    session.record("implementation", request, ok=True)

    def _watch() -> None:
        try:
            timed_out = False
            try:
                stdout, stderr = proc.communicate(
                    input=invocation.stdin,
                    timeout=CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                timed_out = True
                task.request_cancel()
                terminate_child_process(proc)
                stdout, stderr = proc.communicate()

            out = (stdout or "")[:_MAX_COMMAND_OUTPUT_CHARS]
            err = (stderr or "")[:_MAX_COMMAND_OUTPUT_CHARS]
            if timed_out:
                task.mark_failed(f"timed out after {CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS}s")
                console.print(
                    f"[{ERROR}]Claude Code timed out after "
                    f"{CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS} seconds[/]"
                )
                return

            code = proc.returncode
            if task.cancel_requested.is_set() and code != 0:
                task.mark_cancelled()
                if session.history_generation == history_gen_when_started:
                    session.mark_latest(ok=False, kind="implementation")
                console.print(f"[{WARNING}]Claude Code task cancelled.[/]")
                return

            if code == 0:
                task.mark_completed(result="ok")
                console.print(f"[{HIGHLIGHT}]Claude Code completed[/] task {task.task_id}")
                print_command_output(console, out)
                if err:
                    print_command_output(console, err, style=DIM)
                return

            diag = (err or out).strip()[:_SYNTHETIC_DIAG_CHARS]
            error_msg = f"exit code {code}" + (f": {diag}" if diag else "")
            task.mark_failed(error_msg)
            if session.history_generation == history_gen_when_started:
                session.mark_latest(ok=False, kind="implementation")
            console.print(f"[{ERROR}]Claude Code failed (exit {code}):[/]")
            print_command_output(console, out)
            print_command_output(console, err, style=ERROR)
        except Exception as exc:  # noqa: BLE001
            task.mark_failed(str(exc))
            report_exception(exc, context="interactive_shell.claude_code.watch")
            if session.history_generation == history_gen_when_started:
                session.mark_latest(ok=False, kind="implementation")
            console.print(f"[{ERROR}]Claude Code watcher failed:[/] {escape(str(exc))}")

    threading.Thread(target=_watch, daemon=True, name=f"claude-code-{task.task_id}").start()
    console.print(
        f"[{DIM}]Claude Code started — task[/] [bold]{escape(task.task_id)}[/bold]. "
        f"[{HIGHLIGHT}]/tasks[/] [{DIM}]to monitor,[/] "
        f"[{HIGHLIGHT}]/cancel {escape(task.task_id)}[/] [{DIM}]to stop.[/]"
    )
