"""Single-command CLI entrypoints that do not need their own groups."""

from __future__ import annotations

import json
import sys
import time

import click

import platform
from config.version import get_opensre_version
from platform.analytics.cli import (
    capture_update_completed,
    capture_update_failed,
    capture_update_started,
    track_investigation,
)
from platform.analytics.source import EntrypointSource, TriggerMode
from platform.common.exit_codes import ERROR, SUCCESS
from platform.common.runtime_flags import is_json_output, is_yes
from surfaces.cli.constants import ALERT_TEMPLATE_CHOICES


@click.command(name="uninstall")
@click.option("--yes", "-y", "local_yes", is_flag=True, help="Skip the confirmation prompt.")
def uninstall_command(local_yes: bool) -> None:
    """Remove opensre and all local data from this machine."""
    from surfaces.cli.lifecycle.uninstall import run_uninstall

    raise SystemExit(run_uninstall(yes=local_yes or is_yes()))


@click.command(name="update")
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Report whether an update is available without installing.",
)
@click.option("--yes", "-y", "local_yes", is_flag=True, help="Skip the confirmation prompt.")
def update_command(check_only: bool, local_yes: bool) -> None:
    """Check for a newer main build and update if one is available."""
    from surfaces.cli.lifecycle.update import run_update

    capture_update_started(check_only=check_only)
    try:
        exit_code = run_update(check_only=check_only, yes=local_yes or is_yes())
    except Exception as exc:
        capture_update_failed(check_only=check_only, reason=type(exc).__name__)
        raise

    capture_update_completed(
        check_only=check_only,
        updated=exit_code == 0 and not check_only,
    )
    raise SystemExit(exit_code)


@click.command(name="version")
def version_command() -> None:
    """Print detailed version, Python and OS info."""
    if is_json_output():
        click.echo(
            json.dumps(
                {
                    "opensre": get_opensre_version(),
                    "python": platform.python_version(),
                    "os": platform.system().lower(),
                    "arch": platform.machine(),
                }
            )
        )
        return
    click.echo(f"opensre {get_opensre_version()}")
    click.echo(f"Python  {platform.python_version()}")
    click.echo(f"OS      {platform.system().lower()} ({platform.machine()})")


@click.command(name="health")
@click.option("--watch", is_flag=True, help="Continuously refresh the health report.")
@click.option(
    "--rate", default=5, show_default=True, help="Refresh interval in seconds (with --watch)."
)
def health_command(watch: bool, rate: int) -> None:
    """Show a quick health summary of the local agent setup."""
    from config.config import get_environment
    from integrations.store import STORE_PATH
    from integrations.verify import verify_integrations
    from surfaces.interactive_shell.ui.health import render_health_json, render_health_report

    def _run_once() -> int:
        results = verify_integrations()
        environment = get_environment().value

        if is_json_output():
            render_health_json(
                environment=environment,
                integration_store_path=STORE_PATH,
                results=results,
            )
        else:
            from rich.console import Console

            render_health_report(
                console=Console(highlight=False),
                environment=environment,
                integration_store_path=STORE_PATH,
                results=results,
            )

        if any(result.get("status") == "failed" for result in results):
            return ERROR
        return SUCCESS

    if not watch:
        raise SystemExit(_run_once())

    try:
        while True:
            click.clear()
            _run_once()
            time.sleep(rate)
    except KeyboardInterrupt:
        raise SystemExit(SUCCESS) from None


@click.command(name="investigate")
@click.argument(
    "alert_file",
    required=False,
    type=click.Path(),
)
@click.option(
    "--input",
    "-i",
    "input_path",
    default=None,
    type=click.Path(),
    help="Path to an alert file (.json, .md, .txt, ...). Use '-' to read from stdin.",
)
@click.option("--input-json", default=None, help="Inline alert JSON string.")
@click.option("--interactive", is_flag=True, help="Paste an alert JSON payload into the terminal.")
@click.option(
    "--print-template",
    type=click.Choice(ALERT_TEMPLATE_CHOICES),
    default=None,
    help="Print a starter alert JSON template and exit.",
)
@click.option(
    "--output", "-o", default=None, type=click.Path(), help="Output JSON file (default: stdout)."
)
@click.option(
    "--evaluate",
    is_flag=True,
    help="After final diagnosis, LLM-judge vs scoring_points rubric (rubric stripped from agent alert).",
)
def investigate_command(
    alert_file: str | None,
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
    print_template: str | None,
    output: str | None,
    evaluate: bool,
) -> None:
    """Run an RCA investigation against an alert payload."""
    # Treat a bare positional path the same as ``-i <path>``. Lets users type
    # ``opensre investigate alert.json`` instead of the more verbose
    # ``opensre investigate -i alert.json``. If both are given, the explicit
    # flag wins to keep the behaviour predictable.
    if alert_file and not input_path:
        input_path = alert_file

    from surfaces.cli import write_json
    from surfaces.cli.investigation import run_investigation_cli, run_investigation_cli_streaming
    from surfaces.cli.investigation.payload import load_payload
    from tools.investigation.alert_templates import build_alert_template

    try:
        if print_template:
            write_json(build_alert_template(print_template), output)
            raise SystemExit(SUCCESS)

        payload = load_payload(
            input_path=input_path,
            input_json=input_json,
            interactive=interactive,
        )
        trigger_mode = (
            TriggerMode.PASTE
            if interactive
            else (TriggerMode.INLINE_JSON if input_json is not None else TriggerMode.FILE)
        )
        with track_investigation(
            entrypoint=EntrypointSource.CLI_COMMAND,
            trigger_mode=trigger_mode,
            input_path=input_path,
            input_json=input_json,
            interactive=interactive,
            evaluate_requested=evaluate,
        ) as tracker:
            # Only stream the live UI when the user is interactively watching stdout
            # and hasn't asked for machine-readable JSON. Otherwise the spinner and
            # ANSI control codes corrupt the JSON payload that consumers expect on
            # stdout (pipes, redirection, --json, CI logs).
            # --evaluate forces the non-streaming path because the streaming runner
            # does not yet wire opensre_evaluate scoring through the renderer.
            stream_to_stdout = (
                sys.stdout.isatty() and not is_json_output() and output is None and not evaluate
            )
            if stream_to_stdout:
                run_investigation_cli_streaming(raw_alert=payload, tracker=tracker)
            else:
                result = run_investigation_cli(raw_alert=payload, opensre_evaluate=evaluate)
                write_json(result, output)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit(SUCCESS) from None

    raise SystemExit(SUCCESS)
