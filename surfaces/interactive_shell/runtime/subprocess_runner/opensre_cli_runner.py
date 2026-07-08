"""OpenSRE CLI command runner — surface adapter over tools.interactive_shell.cli."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import make_repl_presenter
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import DIM, WARNING
from tools.interactive_shell.cli import (
    INTERACTIVE_OPENSRE_COMMAND_PATHS,
    OPENSRE_BLOCKED_SUBCOMMANDS,
    OpensreCommandClass,
    OpensreExecutionMode,
    OpensreExecutionPlan,
    OpensreRunOutcome,
    OpensreRunResult,
    _run_foreground_via_presenter,
    _run_streaming_via_presenter,
    build_opensre_cli_argv,
    build_opensre_execution_plan,
    classify_opensre_command,
    interactive_wizard_handoff_response_text,
    is_interactive_wizard,
    opensre_confirmation_reason,
)
from tools.interactive_shell.cli import (
    run_opensre_cli_command as _run_opensre_cli_command,
)
from tools.interactive_shell.cli import (
    run_opensre_cli_command_result as _run_opensre_cli_command_result,
)

# Backward-compatible aliases for tests and slash parity.
_INTERACTIVE_OPENSRE_COMMAND_PATHS = INTERACTIVE_OPENSRE_COMMAND_PATHS
_OPENSRE_BLOCKED_SUBCOMMANDS = OPENSRE_BLOCKED_SUBCOMMANDS


def _is_interactive_wizard(tokens: list[str]) -> bool:
    return is_interactive_wizard(tokens)


def _classify_opensre_command(tokens: list[str]) -> str:
    return classify_opensre_command(tokens)


def _opensre_confirmation_reason(tokens: list[str]) -> str:
    return opensre_confirmation_reason(tokens)


def _build_opensre_execution_plan(tokens: list[str]) -> OpensreExecutionPlan:
    return build_opensre_execution_plan(tokens)


def print_interactive_wizard_handoff(console: Console, command_str: str) -> None:
    console.print(
        f"[{WARNING}]`opensre {command_str}` is an interactive wizard "
        "that needs a full terminal.[/]"
    )
    console.print(
        f"[{DIM}]Type [bold]/{command_str}[/bold] directly in this shell to launch it.[/]"
    )


def run_opensre_cli_command(
    args: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    presenter = make_repl_presenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=True,
    )
    return _run_opensre_cli_command(args, presenter)


def run_opensre_cli_command_result(
    args: str,
    session: Session,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> OpensreRunResult:
    presenter = make_repl_presenter(
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=True,
    )
    return _run_opensre_cli_command_result(args, presenter)


# Foreground helpers kept for monkeypatch tests that patch subprocess_runner paths.
def _run_opensre_foreground(
    argv_list: list[str],
    display_command: str,
    session: Session,
    console: Console,
) -> None:
    presenter = make_repl_presenter(session, console, action_already_listed=True)
    _run_foreground_via_presenter(
        presenter,
        argv_list=argv_list,
        display_command=display_command,
    )


def _run_opensre_foreground_streaming(
    argv_list: list[str],
    display_command: str,
    session: Session,
    console: Console,
) -> None:
    presenter = make_repl_presenter(session, console, action_already_listed=True)
    _run_streaming_via_presenter(
        presenter,
        argv_list=argv_list,
        display_command=display_command,
    )


__all__ = [
    "OpensreCommandClass",
    "OpensreExecutionMode",
    "OpensreExecutionPlan",
    "OpensreRunOutcome",
    "OpensreRunResult",
    "_INTERACTIVE_OPENSRE_COMMAND_PATHS",
    "_OPENSRE_BLOCKED_SUBCOMMANDS",
    "_build_opensre_execution_plan",
    "_classify_opensre_command",
    "_is_interactive_wizard",
    "_opensre_confirmation_reason",
    "_run_opensre_foreground",
    "_run_opensre_foreground_streaming",
    "build_opensre_cli_argv",
    "interactive_wizard_handoff_response_text",
    "print_interactive_wizard_handoff",
    "run_opensre_cli_command",
    "run_opensre_cli_command_result",
]
