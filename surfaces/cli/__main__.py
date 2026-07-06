"""OpenSRE CLI - open-source SRE agent for automated incident investigation.

Enable shell tab-completion (add to your shell profile for persistence):

  bash:  eval "$(_OPENSRE_COMPLETE=bash_source opensre)"
  zsh:   eval "$(_OPENSRE_COMPLETE=zsh_source opensre)"
  fish:  _OPENSRE_COMPLETE=fish_source opensre | source
"""

from __future__ import annotations

import os
import signal
import sys
from contextlib import suppress
from typing import TYPE_CHECKING

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()

import click  # noqa: E402

from config.version import get_opensre_version  # noqa: E402
from surfaces.cli.group import LazyRichGroup, ThemeParamType  # noqa: E402
from surfaces.cli.invocation import (  # noqa: E402
    ensure_utf8_stdio,
    is_fast_version_invocation,
    print_fast_version,
    resolve_command_parts,
)
from surfaces.cli.telemetry import (  # noqa: E402
    build_cli_invoked_properties,
    capture_cli_invoked,
    capture_exception,
    capture_first_run_if_needed,
    init_sentry,
    load_structured_error_type,
    render_landing,
    render_structured_error,
    report_exception,
    should_report_exception,
    shutdown_analytics,
)

if TYPE_CHECKING:
    from platform.analytics.provider import Properties

_CAPTURE_CLI_ANALYTICS = "capture_cli_analytics"
_CLI_ANALYTICS_CAPTURED = "cli_analytics_captured"
_CLI_ARGV = "cli_argv"


def _cli_invoked_properties(ctx: click.Context) -> Properties:
    raw_argv = ctx.obj.get(_CLI_ARGV, []) if ctx.obj else []
    command_parts = resolve_command_parts(
        ctx.command,
        raw_argv if isinstance(raw_argv, list) else [],
    )
    obj = ctx.obj if ctx.obj else {}
    return build_cli_invoked_properties(
        entrypoint="opensre",
        command_parts=command_parts,
        json_output=bool(obj.get("json", False)),
        verbose=bool(obj.get("verbose", False)),
        debug=bool(obj.get("debug", False)),
        yes=bool(obj.get("yes", False)),
        interactive=bool(obj.get("interactive", True)),
    )


def _capture_accepted_cli_invocation(ctx: click.Context) -> None:
    if not ctx.obj.get(_CAPTURE_CLI_ANALYTICS, False):
        return
    if ctx.obj.get(_CLI_ANALYTICS_CAPTURED, False):
        return
    ctx.obj[_CLI_ANALYTICS_CAPTURED] = True
    capture_first_run_if_needed()
    capture_cli_invoked(_cli_invoked_properties(ctx))


@click.group(
    cls=LazyRichGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(version=get_opensre_version(), prog_name="opensre")
@click.option(
    "--json", "-j", "json_output", is_flag=True, help="Emit machine-readable JSON output."
)
@click.option("--verbose", is_flag=True, help="Print extra diagnostic information.")
@click.option("--debug", is_flag=True, help="Print debug-level logs and traces.")
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm all interactive prompts.")
@click.option(
    "--interactive/--no-interactive",
    default=True,
    help="Disable the interactive shell and print the landing page instead.",
)
@click.option(
    "--resume",
    "resume_session_id",
    default=None,
    metavar="SESSION-ID",
    help="Resume a previous interactive shell session by ID, prefix, or name substring.",
)
@click.option(
    "--layout",
    type=click.Choice(["classic", "pinned"]),
    default=None,
    help="Interactive-shell layout: 'classic' (scrolling) or 'pinned' (fixed "
    "input bar). Overrides OPENSRE_LAYOUT env var and ~/.opensre/config.yml.",
)
@click.option(
    "--theme",
    type=ThemeParamType(),
    default=None,
    help="Interactive-shell color palette. Overrides OPENSRE_THEME env var "
    "and ~/.opensre/config.yml interactive.theme.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    json_output: bool,
    verbose: bool,
    debug: bool,
    yes: bool,
    interactive: bool,
    resume_session_id: str | None,
    layout: str | None,
    theme: str | None,
) -> None:
    """OpenSRE - open-source SRE agent for automated incident investigation and root cause analysis."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    ctx.obj["verbose"] = verbose
    ctx.obj["debug"] = debug
    ctx.obj["yes"] = yes
    ctx.obj["interactive"] = interactive

    from surfaces.cli.runtime_flags import sync_runtime_flags_from_click

    sync_runtime_flags_from_click(ctx)

    if verbose or debug:
        os.environ["TRACER_VERBOSE"] = "1"

    from config.repl_config import ReplConfig

    _capture_accepted_cli_invocation(ctx)

    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            from surfaces.interactive_shell import run_repl

            config = ReplConfig.load(
                cli_enabled=interactive or resume_session_id is not None,
                cli_layout=layout,
                cli_theme=theme,
            )
            if config.enabled or resume_session_id:
                raise SystemExit(
                    run_repl(
                        config=config,
                        resume_session_id=resume_session_id,
                    )
                )
        click.echo("🚧 OpenSRE is in Public Beta — features may change.", err=True)
        render_landing(cli)
        raise SystemExit(0)

    # Apply interactive.theme / OPENSRE_THEME / --theme for subcommands (onboard, etc.).
    ReplConfig.load(cli_theme=theme)


def _install_sigint_handler() -> None:
    """Handle Ctrl+C between prompts (when prompt_toolkit is not active).

    prompt_toolkit intercepts Ctrl+C internally while a prompt is running, so
    the key binding in prompt_support.py handles that case.  This SIGINT handler
    covers everything else: long-running operations, streaming output, etc.
    """

    def _handler(_signum: int, _frame: object) -> None:
        from platform.terminal.prompt_support import handle_ctrl_c_press

        handle_ctrl_c_press()

    signal.signal(signal.SIGINT, _handler)


def _is_update_invocation(argv: list[str]) -> bool:
    command_parts = resolve_command_parts(cli, argv)
    return bool(command_parts) and command_parts[0] == "update"


def _sentry_entrypoint_for_invocation(argv: list[str]) -> str:
    command_parts = resolve_command_parts(cli, argv)
    if command_parts and command_parts[0] == "debug":
        return "debug"
    return "cli"


def _should_capture_cli_exception(exc: click.ClickException) -> bool:
    """Return whether a Click error represents an unexpected internal failure."""
    return should_report_exception(exc)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``opensre`` console script."""
    ensure_utf8_stdio()
    cli_argv = list(sys.argv[1:] if argv is None else argv)
    if is_fast_version_invocation(cli_argv):
        print_fast_version(cli_argv)
        return 0

    from config.local_env import bootstrap_opensre_env_once

    bootstrap_opensre_env_once(override=False)
    try:
        init_sentry(entrypoint=_sentry_entrypoint_for_invocation(cli_argv))
    except ModuleNotFoundError as exc:
        if exc.name != "sentry_sdk" or not _is_update_invocation(cli_argv):
            raise
    # Wire CLI-flavored implementations into the observability ports
    # (ProgressTracker, debug_print) so any core code under core/domain,
    # tools/investigation, utils that calls into the abstractions routes
    # through the Rich-aware adapters during this process.
    from surfaces.interactive_shell.ui.output.boundary import (
        install_product_adapters,
    )

    install_product_adapters()
    from platform.terminal.prompt_support import (
        install_questionary_ctrl_c_double_exit,
        install_questionary_escape_cancel,
    )

    install_questionary_escape_cancel()
    install_questionary_ctrl_c_double_exit()
    _install_sigint_handler()
    StructuredError = load_structured_error_type()

    try:
        cli(
            args=cli_argv,
            standalone_mode=False,
            obj={_CAPTURE_CLI_ANALYTICS: True, _CLI_ARGV: cli_argv},
        )
    except KeyboardInterrupt:
        # A KeyboardInterrupt that escapes cli() was not handled by our
        # double-exit logic (e.g. click.prompt, an unpatched library prompt).
        # Print a newline so the terminal cursor lands on a clean line, then
        # exit quietly — Click's "Aborted!" message is intentionally suppressed.
        print(flush=True)
        return 0
    except click.Abort:
        # Click raises Abort for some prompt-level cancel paths. Treat it as a
        # clean user cancel, not as an unexpected CLI failure.
        print(flush=True)
        return 0
    except click.ClickException as exc:
        if _should_capture_cli_exception(exc):
            report_exception(exc, context="surfaces.cli.main")
        exc.show()
        return exc.exit_code
    except StructuredError as exc:
        # A structured error raised by non-CLI code (tools/integrations) is not
        # a ClickException, so render it as a clean panel (no traceback) here.
        return render_structured_error(exc)
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        if exc.code is not None:
            click.echo(exc.code, err=True)
            return 1
        return 0
    except BaseException as exc:
        if not isinstance(exc, KeyboardInterrupt):
            capture_exception(exc, context="surfaces.cli.main.unhandled")
            with suppress(Exception):
                import sentry_sdk as _sentry_sdk

                _sentry_sdk.flush(timeout=2)
        raise
    finally:
        shutdown_analytics(flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
