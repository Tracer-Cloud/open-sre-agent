"""OpenSRE CLI command runner — route subcommands to foreground or background."""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.error_handling.exception_reporting import report_exception
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    ActionExecutionMode,
    ActionExecutionPlan,
    ExecutionPolicyResult,
    execution_allowed,
)
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import DIM, ERROR, WARNING, print_command_output

from .background_tasks import start_background_cli_task as _start_background_cli_task_default
from .task_streaming import SHELL_COMMAND_TIMEOUT_SECONDS, _ae_resolve

_OPENSRE_BLOCKED_SUBCOMMANDS: frozenset[str] = frozenset({"agent"})

# Command paths (one or two whitespace-joined tokens) that drive a
# full-TTY interactive wizard — ``questionary`` radio widgets, multi-
# step prompts.
#
# The *slash-command* paths (e.g. ``/onboard``, ``/integrations setup``)
# are safe to run from the REPL because ``dispatch.py`` lists them in
# ``_WAIT_FOR_COMPLETION_COMMANDS`` / ``_EXCLUSIVE_STDIN_SUBCOMMANDS``,
# which pauses the prompt_toolkit Application before the handler runs and
# gives the wizard subprocess exclusive stdin.
#
# The *LLM-classified* path (``cli_exec`` tool with payload ``"onboard"``)
# does NOT have that guarantee — the main loop may already be awaiting the
# next ``prompt_async`` — so we intercept here and tell the user to invoke
# the corresponding slash command instead.
#
# Stored as space-joined paths (e.g. ``"integrations setup"``) so both
# one-token (``"onboard"``) and two-token cases live in a single
# data-driven set; :func:`_is_interactive_wizard` does the lookup.
_INTERACTIVE_OPENSRE_COMMAND_PATHS: frozenset[str] = frozenset(
    {
        "onboard",
        "integrations setup",
    }
)


def _is_interactive_wizard(tokens: list[str]) -> bool:
    """True when ``tokens`` name an opensre subcommand whose Click
    handler drives an interactive wizard (questionary-backed widgets)
    that needs a full TTY.
    """
    if not tokens:
        return False
    one = tokens[0].lower()
    if one in _INTERACTIVE_OPENSRE_COMMAND_PATHS:
        return True
    if len(tokens) < 2:
        return False
    two = f"{one} {tokens[1].lower()}"
    return two in _INTERACTIVE_OPENSRE_COMMAND_PATHS


def print_interactive_wizard_handoff(console: Console, command_str: str) -> None:
    """Print the 'wizard needs a full terminal' guidance for the LLM-classified
    intent path. The slash-command path (e.g. ``/onboard``) now runs the wizard
    directly — this message is only shown when the LLM tries to invoke the wizard
    via ``cli_exec`` where exclusive stdin is not guaranteed.

    Exported (no leading underscore) because it crosses module
    boundaries — Greptile flagged that a private name imported across
    modules creates a hidden public contract.
    """
    console.print(
        f"[{WARNING}]`opensre {command_str}` is an interactive wizard "
        "that needs a full terminal.[/]"
    )
    console.print(
        f"[{DIM}]Type [bold]/{command_str}[/bold] directly in this shell to launch it.[/]"
    )


_READ_ONLY_OPENSRE_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "health",
        "version",
        "list",
        "status",
        "show",
    }
)

# Core RCA entrypoint — users open the REPL to investigate; no extra confirm.
_INVESTIGATION_OPENSRE_SUBCOMMANDS: frozenset[str] = frozenset({"investigate"})


class OpensreCommandClass(StrEnum):
    READ_ONLY = "read_only"
    INVESTIGATION = "investigation"
    MUTATING = "mutating"


class OpensreExecutionMode(StrEnum):
    FOREGROUND = "foreground"
    FOREGROUND_STREAMING = "foreground_streaming"
    BACKGROUND = "background"


class OpensreRunOutcome(StrEnum):
    BLOCKED = "blocked"
    HANDED_OFF = "handed_off"
    EXECUTED_FOREGROUND = "executed_foreground"
    EXECUTED_BACKGROUND = "executed_background"
    DECLINED = "declined"
    INVALID = "invalid"


@dataclass(frozen=True)
class OpensreExecutionPlan:
    classification: OpensreCommandClass
    execution_mode: OpensreExecutionMode
    requires_confirmation: bool
    confirmation_reason: str | None


@dataclass(frozen=True)
class OpensreRunResult:
    outcome: OpensreRunOutcome
    attempted: bool
    display_command: str | None = None


def _classify_opensre_command(tokens: list[str]) -> str:
    first_token = tokens[0].lower()
    if first_token in _READ_ONLY_OPENSRE_SUBCOMMANDS:
        return OpensreCommandClass.READ_ONLY.value
    if first_token in _INVESTIGATION_OPENSRE_SUBCOMMANDS:
        return OpensreCommandClass.INVESTIGATION.value
    if first_token == "fleet":
        subcommand = tokens[1].lower() if len(tokens) > 1 else "list"
        if subcommand in {"list"}:
            return OpensreCommandClass.READ_ONLY.value
        if subcommand == "scan" and "--register" not in tokens[2:]:
            return OpensreCommandClass.READ_ONLY.value
    return OpensreCommandClass.MUTATING.value


def _opensre_confirmation_reason(tokens: list[str]) -> str:
    if tokens[:2] == ["fleet", "scan"] and "--register" in tokens[2:]:
        return "register discovered local AI-agent processes"
    if tokens and tokens[0] == "fleet":
        return "this updates the local AI-agent registry"
    return "this opensre subcommand may change local config or infrastructure"


def _should_run_opensre_in_foreground(tokens: list[str]) -> bool:
    return _build_opensre_execution_plan(tokens).execution_mode in {
        OpensreExecutionMode.FOREGROUND,
        OpensreExecutionMode.FOREGROUND_STREAMING,
    }


def _build_opensre_execution_plan(tokens: list[str]) -> OpensreExecutionPlan:
    """Compute classification + execution mode from one canonical policy table."""
    classification = OpensreCommandClass(_classify_opensre_command(tokens))
    first_token = tokens[0].lower()

    execution_mode = OpensreExecutionMode.BACKGROUND
    if first_token in _READ_ONLY_OPENSRE_SUBCOMMANDS:
        execution_mode = OpensreExecutionMode.FOREGROUND
    elif first_token == "fleet":
        subcommand = tokens[1].lower() if len(tokens) > 1 else "list"
        if subcommand == "watch":
            execution_mode = OpensreExecutionMode.FOREGROUND_STREAMING
        elif subcommand in {"list", "register", "forget", "scan"}:
            execution_mode = OpensreExecutionMode.FOREGROUND

    requires_confirmation = classification is OpensreCommandClass.MUTATING
    reason = (
        _opensre_confirmation_reason([token.lower() for token in tokens])
        if requires_confirmation
        else None
    )
    return OpensreExecutionPlan(
        classification=classification,
        execution_mode=execution_mode,
        requires_confirmation=requires_confirmation,
        confirmation_reason=reason,
    )


def _to_action_execution_plan(plan: OpensreExecutionPlan) -> ActionExecutionPlan:
    mode = ActionExecutionMode.BACKGROUND
    if plan.execution_mode is OpensreExecutionMode.FOREGROUND:
        mode = ActionExecutionMode.FOREGROUND
    elif plan.execution_mode is OpensreExecutionMode.FOREGROUND_STREAMING:
        mode = ActionExecutionMode.FOREGROUND_STREAMING
    if not plan.requires_confirmation:
        policy = ExecutionPolicyResult(
            verdict="allow",
            action_type="cli_command",
            reason=None,
            hint=None,
            shell_classification=plan.classification.value,
        )
    else:
        policy = ExecutionPolicyResult(
            verdict="ask",
            action_type="cli_command",
            reason=plan.confirmation_reason,
            hint="Use a read-only subcommand (health, version, list, status, show)",
            shell_classification=plan.classification.value,
        )
    return ActionExecutionPlan(
        action_type="cli_command",
        classification=plan.classification.value,
        execution_mode=mode,
        policy=policy,
    )


def _run_opensre_foreground(
    argv_list: list[str],
    display_command: str,
    session: ReplSession,
    console: Console,
) -> None:
    console.print(f"[bold]$ {escape(display_command)}[/bold]")
    try:
        result = subprocess.run(
            argv_list,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SHELL_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        print_command_output(console, str(exc.output or ""))
        print_command_output(console, str(exc.stderr or ""), style=ERROR)
        console.print(
            f"[{ERROR}]command timed out after {SHELL_COMMAND_TIMEOUT_SECONDS} seconds[/]"
        )
        session.record("cli_command", display_command, ok=False)
        return
    except Exception as exc:  # noqa: BLE001
        report_exception(exc, context="interactive_shell.opensre_cli.start")
        console.print(f"[{ERROR}]failed to start:[/] {escape(str(exc))}")
        session.record("cli_command", display_command, ok=False)
        return

    print_command_output(console, result.stdout)
    print_command_output(console, result.stderr, style=ERROR)
    ok = result.returncode == 0
    if not ok:
        console.print(f"[{ERROR}]command failed (exit {result.returncode}):[/]")
    session.record("cli_command", display_command, ok=ok)


def _run_opensre_foreground_streaming(
    argv_list: list[str],
    display_command: str,
    session: ReplSession,
    console: Console,
) -> None:
    console.print(f"[bold]$ {escape(display_command)}[/bold]")
    try:
        proc = subprocess.Popen(
            argv_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:  # noqa: BLE001
        report_exception(exc, context="interactive_shell.opensre_cli.start")
        console.print(f"[{ERROR}]failed to start:[/] {escape(str(exc))}")
        session.record("cli_command", display_command, ok=False)
        return

    if proc.stdout is not None:
        for line in proc.stdout:
            print_command_output(console, line)
    code = proc.wait()
    ok = code == 0
    if not ok:
        console.print(f"[{ERROR}]command failed (exit {code}):[/]")
    session.record("cli_command", display_command, ok=ok)


def run_opensre_cli_command(
    args: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    result = run_opensre_cli_command_result(
        args,
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )
    return result.attempted


def run_opensre_cli_command_result(
    args: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> OpensreRunResult:
    """Run an opensre subcommand (not agent).

    Returns a typed outcome so callers can distinguish blocked/declined/
    handed-off/executed states without overloading ``bool``.

    ``confirm_fn`` is forwarded to :func:`execution_allowed` so the
    interactive REPL can route mid-dispatch ``Proceed? [y/N]`` prompts
    through its active prompt_toolkit input — the stdlib ``input()``
    deadlocks against the running ``prompt_async``.
    """
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    if not tokens:
        return OpensreRunResult(outcome=OpensreRunOutcome.INVALID, attempted=False)

    first_token = tokens[0].lower()
    if first_token in _OPENSRE_BLOCKED_SUBCOMMANDS:
        console.print(f"[{ERROR}]Cannot run `opensre {first_token}`: subcommand is blocked.[/]")
        return OpensreRunResult(outcome=OpensreRunOutcome.BLOCKED, attempted=False)

    if _is_interactive_wizard(tokens):
        command_str = " ".join(tokens)
        print_interactive_wizard_handoff(console, command_str)
        session.record("cli_command", f"opensre {command_str}", ok=False)
        return OpensreRunResult(
            outcome=OpensreRunOutcome.HANDED_OFF,
            attempted=True,
            display_command=f"opensre {command_str}",
        )

    plan = _build_opensre_execution_plan(tokens)
    execution_plan = _to_action_execution_plan(plan)

    if not execution_allowed(
        execution_plan.policy,
        session=session,
        console=console,
        action_summary=f"$ opensre {' '.join(tokens)}",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=True,
    ):
        session.record("cli_command", f"opensre {' '.join(tokens)}", ok=False)
        return OpensreRunResult(
            outcome=OpensreRunOutcome.DECLINED,
            attempted=True,
            display_command=f"opensre {' '.join(tokens)}",
        )

    argv_list = [sys.executable, "-m", "app.cli"] + tokens
    display_command = f"opensre {' '.join(tokens)}"
    if execution_plan.execution_mode in {
        ActionExecutionMode.FOREGROUND,
        ActionExecutionMode.FOREGROUND_STREAMING,
    }:
        if execution_plan.execution_mode is ActionExecutionMode.FOREGROUND_STREAMING:
            _run_opensre_foreground_streaming(argv_list, display_command, session, console)
            return OpensreRunResult(
                outcome=OpensreRunOutcome.EXECUTED_FOREGROUND,
                attempted=True,
                display_command=display_command,
            )
        _run_opensre_foreground(argv_list, display_command, session, console)
        return OpensreRunResult(
            outcome=OpensreRunOutcome.EXECUTED_FOREGROUND,
            attempted=True,
            display_command=display_command,
        )

    session.record("cli_command", display_command)
    _ae_resolve("start_background_cli_task", _start_background_cli_task_default)(
        display_command=display_command,
        argv_list=argv_list,
        session=session,
        console=console,
    )
    return OpensreRunResult(
        outcome=OpensreRunOutcome.EXECUTED_BACKGROUND,
        attempted=True,
        display_command=display_command,
    )
