"""Slash commands: /investigate, /template, /last, /save."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.command_registry.types import ExecutionTier, SlashCommand
from app.cli.interactive_shell.error_handling.errors import OpenSREError
from app.cli.interactive_shell.error_handling.exception_reporting import report_exception
from app.cli.interactive_shell.runtime import ReplSession, TaskKind
from app.cli.interactive_shell.runtime.background_runner import (
    start_background_template_investigation,
    start_background_text_investigation,
)
from app.cli.interactive_shell.ui import (
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    print_repl_json,
)
from app.cli.interactive_shell.ui.choice_menu import (
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from app.llm_reasoning_effort import apply_reasoning_effort


def _interactive_template_menu(session: ReplSession, console: Console) -> bool:
    from app.cli.interactive_shell.data_store.constants import ALERT_TEMPLATE_CHOICES

    root = "/template"
    choices: list[tuple[str, str]] = [(c, c) for c in ALERT_TEMPLATE_CHOICES]
    choices.append(("done", "done"))
    while True:
        name = repl_choose_one(
            title="template",
            breadcrumb=root,
            choices=choices,
        )
        if name is None or name == "done":
            return True
        _cmd_template(session, console, [name])
        repl_section_break(console)


def _interactive_investigate_menu(session: ReplSession, console: Console) -> bool:
    from app.cli.interactive_shell.data_store.constants import SAMPLE_ALERT_OPTIONS

    root = "/investigate"
    choices: list[tuple[str, str]] = [
        ("alert.json", "alert.json (bundled demo alert file)"),
    ]
    choices.extend(SAMPLE_ALERT_OPTIONS)
    choices.append(("__browse__", "custom file path…"))
    choices.append(("done", "done"))

    while True:
        target = repl_choose_one(
            title="investigate",
            breadcrumb=root,
            choices=choices,
        )
        if target is None or target == "done":
            return True
        if target == "__browse__":
            custom_path = _prompt_investigate_path(console)
            if custom_path is None:
                continue
            target = custom_path
        _cmd_investigate_file(session, console, [target])
        repl_section_break(console)


def _prompt_investigate_path(console: Console) -> str | None:
    """Prompt for a user-supplied alert path from the investigate picker."""
    console.print()
    console.print(
        f"[{DIM}]Enter a local alert file path (.json/.md/.txt). Use absolute or relative path.[/]"
    )
    try:
        value = console.input(f"[{HIGHLIGHT}]file path> [/]").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return value if value else None


def _cmd_template(session: ReplSession, console: Console, args: list[str]) -> bool:
    from app.cli.interactive_shell.data_store.constants import ALERT_TEMPLATE_CHOICES
    from app.cli.investigation.alert_templates import build_alert_template

    if not args and repl_tty_interactive():
        return _interactive_template_menu(session, console)

    if not args:
        console.print(
            f"[{DIM}]usage:[/] /template <type>  (choices: {', '.join(ALERT_TEMPLATE_CHOICES)})"
        )
        return True

    template_name = args[0].lower()
    try:
        payload = build_alert_template(template_name)
    except ValueError:
        console.print(
            f"[{ERROR}]unknown template:[/] {escape(template_name)}  "
            f"(choices: {', '.join(ALERT_TEMPLATE_CHOICES)})"
        )
        return True

    print_repl_json(console, json.dumps(payload, indent=2))
    return True


def _validate_investigate_args(args: list[str]) -> str | None:
    if not args and repl_tty_interactive():
        return None
    if not args:
        return (
            f"[{DIM}]usage:[/] /investigate <file|template>  "
            f"(e.g. /investigate alert.json or /investigate generic)"
        )
    return None


def _validate_save_args(args: list[str]) -> str | None:
    if not args:
        return f"[{DIM}]usage:[/] /save <path>  (e.g. /save report.md or /save out.json)"
    return None


def _cmd_investigate_file(session: ReplSession, console: Console, args: list[str]) -> bool:
    from app.analytics.cli import track_investigation
    from app.analytics.source import EntrypointSource, TriggerMode
    from app.cli.interactive_shell.data_store.constants import ALERT_TEMPLATE_CHOICES
    from app.cli.investigation import run_investigation_for_session, run_sample_alert_for_session
    from app.cli.investigation.payload import resolve_alert_path

    if not args and repl_tty_interactive():
        return _interactive_investigate_menu(session, console)
    if not args:
        console.print(
            f"[{DIM}]usage:[/] /investigate <file|template>  "
            f"(e.g. /investigate alert.json or /investigate generic)"
        )
        session.mark_latest(ok=False, kind="slash")
        return True

    raw_target = args[0]
    normalized_target = raw_target.strip().lower()
    template_name = normalized_target
    for prefix in ("sample:", "template:"):
        if template_name.startswith(prefix):
            template_name = template_name[len(prefix) :].strip()
            break
    if template_name not in ALERT_TEMPLATE_CHOICES:
        template_name = ""

    # Treat canonical template names as templates even if same-named files exist
    # in the working directory. Users can still force file mode with an explicit
    # path form (for example: ``/investigate ./generic``).
    if template_name:
        if session.background_mode_enabled:
            start_background_template_investigation(
                template_name=template_name,
                session=session,
                console=console,
                display_command=f"/investigate {template_name}",
            )
            session.record("alert", f"/investigate {template_name}")
            return True
        task = session.task_registry.create(
            TaskKind.INVESTIGATION, command=f"/investigate {template_name}"
        )
        task.mark_running()
        try:
            with (
                track_investigation(
                    entrypoint=EntrypointSource.CLI_REPL_FILE,
                    trigger_mode=TriggerMode.FILE,
                    input_path=f"template:{template_name}",
                    interactive=True,
                ),
                apply_reasoning_effort(session.reasoning_effort),
            ):
                suppress = getattr(console, "suppress_prompt_spinner", None)
                if callable(suppress):
                    suppress()
                final_state = run_sample_alert_for_session(
                    template_name=template_name,
                    context_overrides=session.accumulated_context or None,
                    cancel_requested=task.cancel_requested,
                )
        except KeyboardInterrupt:
            task.mark_cancelled()
            console.print(f"[{WARNING}]investigation cancelled.[/]")
            session.record("alert", f"/investigate {template_name}", ok=False)
            session.mark_latest(ok=False, kind="slash")
            return True
        except OpenSREError as exc:
            task.mark_failed(str(exc))
            console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
            if exc.suggestion:
                console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
            session.record("alert", f"/investigate {template_name}", ok=False)
            session.mark_latest(ok=False, kind="slash")
            return True
        except Exception as exc:
            task.mark_failed(str(exc))
            report_exception(exc, context="interactive_shell.investigate_template")
            console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
            session.record("alert", f"/investigate {template_name}", ok=False)
            session.mark_latest(ok=False, kind="slash")
            return True

        root = final_state.get("root_cause")
        task.mark_completed(result=str(root) if root is not None else "")
        session.apply_investigation_result(final_state)
        session.record("alert", f"/investigate {template_name}")
        return True

    path = resolve_alert_path(raw_target)
    if not path.exists():
        console.print(f"[{ERROR}]file not found:[/] {escape(str(path))}")
        session.mark_latest(ok=False, kind="slash")
        return True

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        report_exception(exc, context="interactive_shell.investigate_file.read")
        console.print(f"[{ERROR}]cannot read file:[/] {escape(str(exc))}")
        session.mark_latest(ok=False, kind="slash")
        return True

    if session.background_mode_enabled:
        start_background_text_investigation(
            alert_text=text,
            session=session,
            console=console,
            display_command=f"/investigate {path}",
        )
        session.record("alert", args[0])
        return True

    task = session.task_registry.create(TaskKind.INVESTIGATION, command=f"/investigate {path}")
    task.mark_running()
    try:
        with (
            track_investigation(
                entrypoint=EntrypointSource.CLI_REPL_FILE,
                trigger_mode=TriggerMode.FILE,
                input_path=str(path),
                interactive=True,
            ),
            apply_reasoning_effort(session.reasoning_effort),
        ):
            suppress = getattr(console, "suppress_prompt_spinner", None)
            if callable(suppress):
                suppress()
            final_state = run_investigation_for_session(
                alert_text=text,
                context_overrides=session.accumulated_context or None,
                cancel_requested=task.cancel_requested,
            )
    except KeyboardInterrupt:
        task.mark_cancelled()
        console.print(f"[{WARNING}]investigation cancelled.[/]")
        session.record("alert", args[0], ok=False)
        session.mark_latest(ok=False, kind="slash")
        return True
    except OpenSREError as exc:
        task.mark_failed(str(exc))
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        if exc.suggestion:
            console.print(f"[{WARNING}]suggestion:[/] {escape(exc.suggestion)}")
        session.record("alert", args[0], ok=False)
        session.mark_latest(ok=False, kind="slash")
        return True
    except Exception as exc:
        task.mark_failed(str(exc))
        report_exception(exc, context="interactive_shell.investigate_file")
        console.print(f"[{ERROR}]investigation failed:[/] {escape(str(exc))}")
        session.record("alert", args[0], ok=False)
        session.mark_latest(ok=False, kind="slash")
        return True

    root = final_state.get("root_cause")
    task.mark_completed(result=str(root) if root is not None else "")
    session.apply_investigation_result(final_state)
    session.record("alert", f"/investigate {raw_target}")
    return True


def _cmd_last(session: ReplSession, console: Console, _args: list[str]) -> bool:
    if session.last_state is None:
        console.print(f"[{DIM}]no investigation in this session yet.[/]")
        return True

    from rich.markdown import Markdown
    from rich.padding import Padding
    from rich.rule import Rule

    root_cause = session.last_state.get("root_cause", "")
    report = session.last_state.get("problem_md") or session.last_state.get("slack_message") or ""

    if not root_cause and not report:
        console.print(f"[{DIM}]last investigation has no report content.[/]")
        return True

    for title, body in (("Root Cause", root_cause), ("Report", report)):
        if not body:
            continue
        console.print()
        console.print(Rule(f"[bold {HIGHLIGHT}] {title} [/]", style=DIM, align="left"))
        console.print(Padding(Markdown(str(body).strip()), (1, 2)))

    return True


def _cmd_save(session: ReplSession, console: Console, args: list[str]) -> bool:
    if session.last_state is None:
        console.print(f"[{DIM}]nothing to save — run an investigation first.[/]")
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
        console.print(f"[{HIGHLIGHT}]saved:[/] {escape(str(dest))}")
    except Exception as exc:
        report_exception(exc, context="interactive_shell.save_report")
        console.print(f"[{ERROR}]save failed:[/] {escape(str(exc))}")
    return True


_TEMPLATE_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("generic", "generic alert JSON template"),
    ("datadog", "Datadog monitor alert template"),
    ("grafana", "Grafana alert template"),
    ("honeycomb", "Honeycomb trigger template"),
    ("coralogix", "Coralogix alert template"),
    ("splunk", "Splunk alert template"),
)

_INVESTIGATE_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("alert.json", "run bundled demo alert file"),
    ("generic", "run generic sample alert"),
    ("datadog", "run Datadog sample alert"),
    ("grafana", "run Grafana sample alert"),
    ("honeycomb", "run Honeycomb sample alert"),
    ("coralogix", "run Coralogix sample alert"),
    ("splunk", "run Splunk sample alert"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/template",
        "Print a starter alert JSON template.",
        _cmd_template,
        usage=(
            "/template",
            "/template generic",
            "/template datadog",
            "/template grafana",
            "/template honeycomb",
            "/template coralogix",
            "/template splunk",
        ),
        notes=("In a TTY, bare /template opens an interactive menu.",),
        first_arg_completions=_TEMPLATE_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/investigate",
        "Run an RCA investigation from a file or sample template.",
        _cmd_investigate_file,
        usage=(
            "/investigate <file|template>",
            "/investigate alert.json",
            "/investigate generic",
        ),
        notes=("In a TTY, bare /investigate opens runnable demo/template options.",),
        first_arg_completions=_INVESTIGATE_FIRST_ARGS,
        execution_tier=ExecutionTier.SAFE,
        validate_args=_validate_investigate_args,
    ),
    SlashCommand(
        "/last",
        "Reprint the most recent investigation report.",
        _cmd_last,
        execution_tier=ExecutionTier.SAFE,
    ),
    SlashCommand(
        "/save",
        "Save the last investigation to a file.",
        _cmd_save,
        usage=("/save <path>",),
        execution_tier=ExecutionTier.ELEVATED,
        validate_args=_validate_save_args,
    ),
]

__all__ = ["COMMANDS"]
