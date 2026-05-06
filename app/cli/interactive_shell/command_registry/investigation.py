"""Slash commands: /investigate, /template, /last, /save."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry.types import SlashCommand
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.tasks import TaskKind
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD, TERMINAL_ERROR
from app.cli.support.errors import OpenSREError
from app.utils.sentry_sdk import capture_exception


def _cmd_template(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from app.cli.investigation.alert_templates import build_alert_template
    from app.cli.support.constants import ALERT_TEMPLATE_CHOICES

    if not args:
        console.print(
            f"[dim]usage:[/dim] /template <type>  (choices: {', '.join(ALERT_TEMPLATE_CHOICES)})"
        )
        return True

    template_name = args[0].lower()
    try:
        payload = build_alert_template(template_name)
    except ValueError:
        console.print(
            f"[{TERMINAL_ERROR}]unknown template:[/] {escape(template_name)}  "
            f"(choices: {', '.join(ALERT_TEMPLATE_CHOICES)})"
        )
        return True

    console.print_json(json.dumps(payload, indent=2))
    return True


def _cmd_investigate_file(session: ReplSession, console: Console, args: list[str]) -> bool:
    from app.cli.investigation import run_investigation_for_session

    if not args:
        console.print("[dim]usage:[/dim] /investigate <file>")
        return True

    path = Path(args[0])
    if not path.exists():
        console.print(f"[{TERMINAL_ERROR}]file not found:[/] {escape(str(path))}")
        return True

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[{TERMINAL_ERROR}]cannot read file:[/] {escape(str(exc))}")
        return True

    task = session.task_registry.create(TaskKind.INVESTIGATION)
    task.mark_running()
    try:
        final_state = run_investigation_for_session(
            alert_text=text,
            context_overrides=session.accumulated_context or None,
            cancel_requested=task.cancel_requested,
        )
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print("[yellow]investigation cancelled.[/yellow]")
        session.record("alert", args[0], ok=False)
        return True
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{TERMINAL_ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[yellow]suggestion:[/yellow] {escape(exc.suggestion)}")
        session.record("alert", args[0], ok=False)
        return True
    except Exception as exc:  # noqa: BLE001
        task.mark_failed(str(exc))
        capture_exception(exc)
        console.print(f"[{TERMINAL_ERROR}]investigation failed:[/] {escape(str(exc))}")
        session.record("alert", args[0], ok=False)
        return True

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.last_state = final_state
    # Match `_run_new_alert` in loop.py: inherit service / cluster / region
    # across subsequent investigations in the same REPL session.
    session.accumulate_from_state(final_state)
    session.record("alert", f"/investigate {args[0]}")
    return True


def _cmd_last(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if session.last_state is None:
        console.print("[dim]no investigation in this session yet.[/dim]")
        return True

    root_cause = session.last_state.get("root_cause", "")
    report = session.last_state.get("problem_md") or session.last_state.get("slack_message") or ""

    if root_cause:
        console.print(f"[{TERMINAL_ACCENT_BOLD}]root cause:[/] {escape(str(root_cause))}")
    if report:
        console.print(escape(str(report)))
    if not root_cause and not report:
        console.print("[dim]last investigation has no report content.[/dim]")
    return True


def _cmd_save(session: ReplSession, console: Console, args: list[str]) -> bool:
    if session.last_state is None:
        console.print("[dim]nothing to save — run an investigation first.[/dim]")
        return True

    if not args:
        console.print("[dim]usage:[/dim] /save <path>  (e.g. /save report.md or /save out.json)")
        return True

    dest = Path(args[0])
    try:
        if dest.suffix.lower() == ".json":
            dest.write_text(json.dumps(session.last_state, indent=2, default=str), encoding="utf-8")
        else:
            root_cause = session.last_state.get("root_cause", "")
            report = (
                session.last_state.get("problem_md")
                or session.last_state.get("slack_message")
                or ""
            )
            lines = []
            if root_cause:
                lines.append(f"## Root Cause\n\n{root_cause}\n")
            if report:
                lines.append(f"## Report\n\n{report}\n")
            dest.write_text("\n".join(lines) or "(no report content)", encoding="utf-8")
        console.print(f"[green]saved:[/green] {escape(str(dest))}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[{TERMINAL_ERROR}]save failed:[/] {escape(str(exc))}")
    return True


_TEMPLATE_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("generic", "generic alert JSON template"),
    ("datadog", "Datadog monitor alert template"),
    ("grafana", "Grafana alert template"),
    ("honeycomb", "Honeycomb trigger template"),
    ("coralogix", "Coralogix alert template"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/template",
        "print a starter alert JSON template "
        "('/template generic|datadog|grafana|honeycomb|coralogix')",
        _cmd_template,
        first_arg_completions=_TEMPLATE_FIRST_ARGS,
    ),
    SlashCommand(
        "/investigate",
        "run an RCA investigation from a file ('/investigate <file>')",
        _cmd_investigate_file,
    ),
    SlashCommand("/last", "reprint the most recent investigation report", _cmd_last),
    SlashCommand("/save", "save last investigation to a file ('/save <path>')", _cmd_save),
]

__all__ = ["COMMANDS"]
