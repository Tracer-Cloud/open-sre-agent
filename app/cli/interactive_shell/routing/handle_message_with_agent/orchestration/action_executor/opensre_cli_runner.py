"""OpenSRE CLI command runner — route subcommands to foreground or background."""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import DIM, ERROR, WARNING, print_command_output
from app.cli.support.exception_reporting import report_exception

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


def _classify_opensre_command(tokens: list[str]) -> str:
    first_token = tokens[0].lower()
    if first_token in _READ_ONLY_OPENSRE_SUBCOMMANDS:
        return "read_only"
    if first_token in _INVESTIGATION_OPENSRE_SUBCOMMANDS:
        return "investigation"
    if first_token == "agents":
        subcommand = tokens[1].lower() if len(tokens) > 1 else "list"
        if subcommand in {"list"}:
            return "read_only"
        if subcommand == "scan" and "--register" not in tokens[2:]:
            return "read_only"
    return "mutating"


def _opensre_confirmation_reason(tokens: list[str]) -> str:
    if tokens[:2] == ["agents", "scan"] and "--register" in tokens[2:]:
        return "register discovered local AI-agent processes"
    if tokens and tokens[0] == "agents":
        return "this updates the local AI-agent registry"
    return "this opensre subcommand may change local config or infrastructure"


def _should_run_opensre_in_foreground(tokens: list[str]) -> bool:
    first_token = tokens[0].lower()
    if first_token in _READ_ONLY_OPENSRE_SUBCOMMANDS:
        return True
    if first_token == "agents":
        subcommand = tokens[1].lower() if len(tokens) > 1 else "list"
        return subcommand in {"list", "register", "forget", "scan", "watch"}
    return False


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
    """Run an opensre subcommand (not agent).

    Returns True if the command was attempted (regardless of success),
    False if the subcommand is blocked or args are empty.

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
        return False

    first_token = tokens[0].lower()
    if first_token in _OPENSRE_BLOCKED_SUBCOMMANDS:
        console.print(f"[{ERROR}]Cannot run `opensre {first_token}`: subcommand is blocked.[/]")
        return False

    if _is_interactive_wizard(tokens):
        command_str = " ".join(tokens)
        print_interactive_wizard_handoff(console, command_str)
        session.record("cli_command", f"opensre {command_str}", ok=False)
        # True = wizard exists and was handed off; the ``_OPENSRE_BLOCKED_SUBCOMMANDS`` branch
        # above returns False for "shouldn't run at all".
        return True

    command_classification = _classify_opensre_command(tokens)
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
        ExecutionPolicyResult,
        execution_allowed,
    )

    if command_classification in {"read_only", "investigation"}:
        policy_result = ExecutionPolicyResult(
            verdict="allow",
            action_type="cli_command",
            reason=None,
            hint=None,
            shell_classification=command_classification,
        )
    else:
        policy_result = ExecutionPolicyResult(
            verdict="ask",
            action_type="cli_command",
            reason=_opensre_confirmation_reason([token.lower() for token in tokens]),
            hint="Use a read-only subcommand (health, version, list, status, show)",
            shell_classification=command_classification,
        )

    if not execution_allowed(
        policy_result,
        session=session,
        console=console,
        action_summary=f"$ opensre {' '.join(tokens)}",
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=True,
    ):
        session.record("cli_command", f"opensre {' '.join(tokens)}", ok=False)
        return True

    argv_list = [sys.executable, "-m", "app.cli"] + tokens
    display_command = f"opensre {' '.join(tokens)}"
    if _should_run_opensre_in_foreground(tokens):
        if [token.lower() for token in tokens[:2]] == ["agents", "watch"]:
            _run_opensre_foreground_streaming(argv_list, display_command, session, console)
            return True
        _run_opensre_foreground(argv_list, display_command, session, console)
        return True

    session.record("cli_command", display_command)
    _ae_resolve("start_background_cli_task", _start_background_cli_task_default)(
        display_command=display_command,
        argv_list=argv_list,
        session=session,
        console=console,
    )
    return True
