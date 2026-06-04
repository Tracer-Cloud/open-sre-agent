from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING
from app.remediation.classifier import classify_remediation_steps
from app.remediation.models import SafetyLevel


def _cmd_remediate(session: ReplSession, console: Console, args: list[str]) -> bool:
    if session.last_state is None:
        console.print(f"[{DIM}]no investigation in this session yet — run /investigate first.[/]")
        return True

    remediation_steps: list[str] = list(session.last_state.get("remediation_steps") or [])

    if not remediation_steps:
        console.print(f"[{DIM}]no remediation steps found in the last investigation.[/]")
        return True

    auto_flag = "--auto" in args or "-a" in args
    steps = classify_remediation_steps(remediation_steps)

    if not steps:
        console.print(f"[{WARNING}]could not classify any remediation steps.[/]")
        return True

    _print_remediation_plan(console, steps)

    safe_actions = [s for s in steps if s.safety_level is SafetyLevel.safe]
    elevated_actions = [s for s in steps if s.safety_level is SafetyLevel.elevated]
    manual = [s for s in steps if s.safety_level is SafetyLevel.manual]

    # Auto-execute safe (read-only) actions — they bypass confirmation
    for action in safe_actions:
        console.print(f"\n[{HIGHLIGHT}]safe action — executing:[/] {escape(action.command)}")
        _execute_and_print(console, action)

    if not elevated_actions and not manual:
        return True

    if elevated_actions:
        if auto_flag:
            console.print(f"\n[{HIGHLIGHT}]auto-remediate mode: executing elevated actions...[/]")
            for action in elevated_actions:
                _execute_and_print(console, action)
        else:
            console.print(f"\n[{WARNING}]elevated actions require confirmation:[/]")
            for action in elevated_actions:
                label = f"Execute: {action.command}"
                confirmed = _confirm_action(console, label)
                if confirmed:
                    _execute_and_print(console, action)
                else:
                    console.print(f"  [{WARNING}]skipped[/] {escape(action.command)}")

    if manual:
        _print_manual_steps(console, manual)

    return True


def _print_remediation_plan(console: Console, actions: list) -> None:
    table = Table(title="Remediation Plan", title_style="bold", box=None)
    table.add_column("#", style="dim", no_wrap=True)
    table.add_column("Action", style="bold")
    table.add_column("Safety", no_wrap=True)
    table.add_column("Command")

    for i, action in enumerate(actions, 1):
        safety_style = {
            SafetyLevel.safe: HIGHLIGHT,
            SafetyLevel.elevated: WARNING,
            SafetyLevel.manual: ERROR,
        }.get(action.safety_level, DIM)
        table.add_row(
            str(i),
            escape(action.description[:80]),
            f"[{safety_style}]{action.safety_level.value}[/]",
            escape(action.command[:60]) if action.command else "[dim]manual[/]",
        )

    console.print()
    console.print(table)
    console.print()


def _execute_and_print(console: Console, action) -> None:
    from app.remediation.executor import execute_remediation_action

    console.print(f"  [{HIGHLIGHT}]executing:[/] {escape(action.command)}")
    result = execute_remediation_action(action)
    if result.success:
        console.print(f"  [{HIGHLIGHT}]success:[/] {escape(result.output[:200])}")
    else:
        console.print(f"  [{ERROR}]failed:[/] {escape(result.error or 'unknown error')}")


def _print_manual_steps(console: Console, actions: list) -> None:
    console.print(f"\n[{WARNING}]manual steps — execute outside OpenSRE:[/]")
    for i, action in enumerate(actions, 1):
        console.print(f"  {i}. {escape(action.description)}")


def _confirm_action(console: Console, label: str) -> bool:
    try:
        answer = console.input(f"  Confirm — {label} [y/N]? ")
        return answer.strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/remediate",
        "Classify and execute remediation steps from the last investigation.",
        _cmd_remediate,
        usage=("/remediate", "/remediate --auto", "/remediate -a"),
        examples=(
            "/remediate             Show plan and confirm each elevated action",
            "/remediate --auto       Execute all actions without prompting",
        ),
        first_arg_completions=(
            ("--auto", "Execute all actions without prompting"),
            ("-a", "Short flag for --auto"),
        ),
        execution_tier=ExecutionTier.ELEVATED,
    ),
]
