"""Slash commands: /investigate, /template, /last, /save."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from config.llm_reasoning_effort import apply_reasoning_effort
from core.agent_harness.session.terminal_access import (
    background_mode_enabled,
    session_terminal,
)
from platform.common.task_types import TaskRecord
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.background.runner import (
    start_background_template_investigation,
    start_background_text_investigation,
)
from surfaces.interactive_shell.ui import (
    DIM,
    ERROR,
    HIGHLIGHT,
    print_repl_json,
)
from surfaces.interactive_shell.ui.components.choice_menu import (
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from surfaces.interactive_shell.ui.foreground_investigation import run_foreground_investigation
from surfaces.interactive_shell.ui.investigation_outcome import (
    InvestigationOutcome,
    normalize_investigation_target,
)
from surfaces.interactive_shell.utils.error_handling.exception_reporting import report_exception
from surfaces.interactive_shell.utils.telemetry.investigation_analytics import (
    publish_investigation_outcome_analytics,
)
from surfaces.interactive_shell.utils.telemetry.turn_outcome import (
    format_investigation_outcome,
    format_investigation_terminal_outcome,
)


def _interactive_template_menu(session: Session, console: Console) -> bool:
    from surfaces.cli.constants import ALERT_TEMPLATE_CHOICES

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


def _queue_investigate_target(session: Session, target: str) -> None:
    """Defer a menu selection to a normal ``/investigate <target>`` turn.

    The interactive picker needs exclusive stdin, but long-running RCA must not
    hold it — queue the resolved target so the loop auto-submits it on the next
    prompt iteration without ``queue.join()`` blocking.
    """
    session.terminal.set_auto_command(f"/investigate {target}")


def _interactive_investigate_menu(session: Session, console: Console) -> bool:
    from surfaces.cli.constants import SAMPLE_ALERT_OPTIONS

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
        _queue_investigate_target(session, target)
        return True


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


def _cmd_template(session: Session, console: Console, args: list[str]) -> bool:
    from surfaces.cli.constants import ALERT_TEMPLATE_CHOICES
    from tools.investigation.alert_templates import build_alert_template

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


def _stage_investigation_turn_telemetry(session: Session, outcome: InvestigationOutcome) -> None:
    """Stage LLM run metadata and structured errors for this turn's recorder flush."""
    from core.agent_harness.accounting.token_accounting import LlmRunInfo, record_llm_turn

    if outcome.llm_input_tokens or outcome.llm_output_tokens:
        record_llm_turn(
            session,
            prompt="",
            response="",
            input_tokens=outcome.llm_input_tokens,
            output_tokens=outcome.llm_output_tokens,
        )

    terminal = session_terminal(session)
    if terminal is None:
        return

    if outcome.llm_model or outcome.llm_input_tokens or outcome.llm_output_tokens:
        terminal.set_pending_turn_llm(
            LlmRunInfo(
                model=outcome.llm_model or None,
                provider=outcome.llm_provider or None,
                latency_ms=outcome.duration_ms or None,
                input_tokens=outcome.llm_input_tokens or None,
                output_tokens=outcome.llm_output_tokens or None,
            )
        )
    if outcome.status == "failed":
        terminal.set_pending_turn_error(
            outcome.failure_category or "unknown",
            outcome.error_message or "investigation failed",
        )


def _record_investigation_turn(
    session: Session,
    *,
    command_line: str,
    outcome: InvestigationOutcome,
) -> None:
    ok = outcome.status == "completed"
    response_text = format_investigation_terminal_outcome(
        command_line,
        target=outcome.target,
        ok=ok,
        final_state=outcome.final_state,
        error_message=outcome.error_message,
        status=outcome.status,
    )
    session.record(
        "alert",
        command_line,
        ok=ok,
        response_text=response_text,
    )
    if not ok:
        session.mark_latest(ok=False, kind="slash")
    if outcome.investigation_id:
        session.last_investigation_id = outcome.investigation_id
    _stage_investigation_turn_telemetry(session, outcome)
    publish_investigation_outcome_analytics(outcome)


def _cmd_investigate_file(session: Session, console: Console, args: list[str]) -> bool:
    from platform.analytics.cli import track_investigation
    from platform.analytics.source import EntrypointSource, TriggerMode
    from surfaces.cli.constants import ALERT_TEMPLATE_CHOICES
    from surfaces.cli.investigation.payload import resolve_alert_path
    from surfaces.interactive_shell.runtime.investigation_adapter import (
        run_investigation_for_session,
        run_sample_alert_for_session,
    )

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
        target_slug = normalize_investigation_target(template_name)
        if background_mode_enabled(session):
            start_background_template_investigation(
                template_name=template_name,
                session=session,
                console=console,
                display_command=f"/investigate {template_name}",
                investigation_target=target_slug,
            )
            session.record(
                "alert",
                f"/investigate {template_name}",
                response_text=format_investigation_outcome(
                    target_slug,
                    background=True,
                ),
            )
            return True

        def _run_template(task: TaskRecord) -> dict[str, object]:
            with (
                track_investigation(
                    entrypoint=EntrypointSource.CLI_REPL_FILE,
                    trigger_mode=TriggerMode.FILE,
                    input_path=f"template:{template_name}",
                    interactive=True,
                    session=session,
                    investigation_target=target_slug,
                ),
                apply_reasoning_effort(session.reasoning_effort),
            ):
                return run_sample_alert_for_session(
                    template_name=template_name,
                    context_overrides=session.accumulated_context or None,
                    cancel_requested=task.cancel_requested,
                )

        command_line = f"/investigate {template_name}"
        outcome = run_foreground_investigation(
            session=session,
            console=console,
            task_command=command_line,
            run=_run_template,
            exception_context="surfaces.interactive_shell.investigate_template",
            target=target_slug,
        )
        _record_investigation_turn(session, command_line=command_line, outcome=outcome)
        return True

    path = resolve_alert_path(raw_target)
    if not path.exists():
        console.print(f"[{ERROR}]file not found:[/] {escape(str(path))}")
        session.mark_latest(ok=False, kind="slash")
        return True

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        report_exception(exc, context="surfaces.interactive_shell.investigate_file.read")
        console.print(f"[{ERROR}]cannot read file:[/] {escape(str(exc))}")
        session.mark_latest(ok=False, kind="slash")
        return True

    if background_mode_enabled(session):
        target_slug = normalize_investigation_target(raw_target, path=path)
        start_background_text_investigation(
            alert_text=text,
            session=session,
            console=console,
            display_command=f"/investigate {path}",
            investigation_target=target_slug,
        )
        session.record(
            "alert",
            args[0],
            response_text=format_investigation_outcome(target_slug, background=True),
        )
        return True

    target_slug = normalize_investigation_target(raw_target, path=path)

    def _run_file(task: TaskRecord) -> dict[str, object]:
        with (
            track_investigation(
                entrypoint=EntrypointSource.CLI_REPL_FILE,
                trigger_mode=TriggerMode.FILE,
                input_path=str(path),
                interactive=True,
                session=session,
                investigation_target=target_slug,
            ),
            apply_reasoning_effort(session.reasoning_effort),
        ):
            return run_investigation_for_session(
                alert_text=text,
                context_overrides=session.accumulated_context or None,
                cancel_requested=task.cancel_requested,
            )

    command_line = f"/investigate {raw_target}"
    outcome = run_foreground_investigation(
        session=session,
        console=console,
        task_command=f"/investigate {path}",
        run=_run_file,
        exception_context="surfaces.interactive_shell.investigate_file",
        target=target_slug,
    )
    _record_investigation_turn(session, command_line=command_line, outcome=outcome)
    return True


def _cmd_last(session: Session, console: Console, _args: list[str]) -> bool:
    if session.last_state is None:
        console.print(f"[{DIM}]no investigation in this session yet.[/]")
        return True

    root_cause = session.last_state.get("root_cause", "")
    report = session.last_state.get("problem_md") or session.last_state.get("slack_message") or ""

    if not root_cause and not report:
        console.print(f"[{DIM}]last investigation has no report content.[/]")
        return True

    render_investigation_report(
        console,
        root_cause=str(root_cause),
        report=str(report),
    )
    return True


def render_investigation_report(
    console: Console,
    *,
    root_cause: str,
    report: str,
) -> None:
    """Render root cause and report sections shared by /last and /rca show."""
    from rich.markdown import Markdown
    from rich.padding import Padding
    from rich.rule import Rule

    for title, body in (("Root Cause", root_cause), ("Report", report)):
        if not body:
            continue
        console.print()
        console.print(Rule(f"[bold {HIGHLIGHT}] {title} [/]", style=DIM, align="left"))
        console.print(Padding(Markdown(str(body).strip()), (1, 2)))


def write_investigation_export(
    dest: Path,
    *,
    root_cause: str = "",
    report: str = "",
    full_state: dict[str, object] | None = None,
) -> None:
    """Write investigation content to ``dest`` as markdown or JSON."""
    if full_state is not None:
        if not root_cause:
            root_cause = str(full_state.get("root_cause") or "")
        if not report:
            report = str(
                full_state.get("problem_md")
                or full_state.get("slack_message")
                or full_state.get("report")
                or ""
            )

    if dest.suffix.lower() == ".json":
        payload = dict(full_state) if full_state is not None else {}
        if root_cause:
            payload.setdefault("root_cause", root_cause)
        if report:
            payload.setdefault("problem_md", report)
            payload.setdefault("report", report)
        dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return

    lines: list[str] = []
    if root_cause:
        lines.append(f"## Root Cause\n\n{root_cause}\n")
    if report:
        lines.append(f"## Report\n\n{report}\n")
    dest.write_text("\n".join(lines) or "(no report content)", encoding="utf-8")


def _cmd_save(session: Session, console: Console, args: list[str]) -> bool:
    if session.last_state is None:
        console.print(f"[{DIM}]nothing to save — run an investigation first.[/]")
        return True

    dest = Path(args[0])
    try:
        write_investigation_export(dest, full_state=session.last_state)
        console.print(f"[{HIGHLIGHT}]saved:[/] {escape(str(dest))}")
    except Exception as exc:
        report_exception(exc, context="surfaces.interactive_shell.save_report")
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
        notes=(
            "In a TTY, bare /investigate opens runnable demo/template options.",
            "Menu selections queue a normal /investigate <target> turn so the prompt "
            "stays free during RCA.",
        ),
        first_arg_completions=_INVESTIGATE_FIRST_ARGS,
        validate_args=_validate_investigate_args,
    ),
    SlashCommand(
        "/last",
        "Reprint the most recent investigation report.",
        _cmd_last,
    ),
    SlashCommand(
        "/save",
        "Save the last investigation to a file.",
        _cmd_save,
        usage=("/save <path>",),
        validate_args=_validate_save_args,
    ),
]

__all__ = ["COMMANDS"]
