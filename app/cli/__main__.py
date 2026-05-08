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

import click
from dotenv import load_dotenv

from app.analytics.cli import build_cli_invoked_properties, capture_cli_invoked
from app.analytics.provider import Properties, capture_first_run_if_needed, shutdown_analytics
from app.cli.commands import register_commands
from app.cli.support.exception_reporting import report_exception, should_report_exception
from app.cli.support.layout import RichGroup, render_landing
from app.cli.support.prompt_support import (
    handle_ctrl_c_press,
    install_questionary_ctrl_c_double_exit,
    install_questionary_escape_cancel,
)
from app.utils.sentry_sdk import capture_exception, init_sentry
from app.version import get_version

_CAPTURE_CLI_ANALYTICS = "capture_cli_analytics"
_CLI_ANALYTICS_CAPTURED = "cli_analytics_captured"
_CLI_ARGV = "cli_argv"


def _ensure_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr so the themed UI renders on legacy
    Windows consoles (cp1252) without UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")


def _option_value_count(command: click.Command, token: str) -> int:
    for param in command.params:
        if not isinstance(param, click.Option):
            continue
        if token not in (*param.opts, *param.secondary_opts):
            continue
        if param.is_flag or param.count:
            return 0
        return max(param.nargs, 1)
    return 0


def _resolve_command_parts(command: click.Command, argv: list[str]) -> list[str]:
    """Resolve nested Click command names without recording option values."""
    parts: list[str] = []
    current = command
    skip_values = 0

    for token in argv:
        if skip_values:
            skip_values -= 1
            continue
        if token == "--":
            break
        if token.startswith("-") and token != "-":
            if "=" not in token:
                skip_values = _option_value_count(current, token)
            continue
        if not isinstance(current, click.Group):
            continue

        subcommand = current.get_command(click.Context(current), token)
        if subcommand is None:
            continue

        parts.append(token)
        current = subcommand

    return parts


def _cli_invoked_properties(ctx: click.Context) -> Properties:
    raw_argv = ctx.obj.get(_CLI_ARGV, []) if ctx.obj else []
    command_parts = _resolve_command_parts(
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
    cls=RichGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(version=get_version(), prog_name="opensre")
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
    "--layout",
    type=click.Choice(["classic", "pinned"]),
    default=None,
    help="Interactive-shell layout: 'classic' (scrolling) or 'pinned' (fixed "
    "input bar). Overrides OPENSRE_LAYOUT env var and ~/.config/opensre/config.yml.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    json_output: bool,
    verbose: bool,
    debug: bool,
    yes: bool,
    interactive: bool,
    layout: str | None,
) -> None:
    """OpenSRE - open-source SRE agent for automated incident investigation and root cause analysis."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    ctx.obj["verbose"] = verbose
    ctx.obj["debug"] = debug
    ctx.obj["yes"] = yes
    ctx.obj["interactive"] = interactive

    if verbose or debug:
        os.environ["TRACER_VERBOSE"] = "1"

    _capture_accepted_cli_invocation(ctx)

    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            from app.cli.interactive_shell import run_repl
            from app.cli.interactive_shell.config import ReplConfig

            config = ReplConfig.load(
                cli_enabled=interactive,
                cli_layout=layout,
            )
            if config.enabled:
                raise SystemExit(run_repl(config=config))
        click.echo("🚧 OpenSRE is in Public Beta — features may change.", err=True)
        render_landing()
        raise SystemExit(0)


register_commands(cli)


def _install_sigint_handler() -> None:
    """Handle Ctrl+C between prompts (when prompt_toolkit is not active).

    prompt_toolkit intercepts Ctrl+C internally while a prompt is running, so
    the key binding in prompt_support.py handles that case.  This SIGINT handler
    covers everything else: long-running operations, streaming output, etc.
    """

    def _handler(_signum: int, _frame: object) -> None:
        handle_ctrl_c_press()

    signal.signal(signal.SIGINT, _handler)


def _is_update_invocation(argv: list[str]) -> bool:
    command_parts = _resolve_command_parts(cli, argv)
    return bool(command_parts) and command_parts[0] == "update"


def _should_capture_cli_exception(exc: click.ClickException) -> bool:
    """Return whether a Click error represents an unexpected internal failure."""
    return should_report_exception(exc)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``opensre`` console script."""
    _ensure_utf8_stdio()
    load_dotenv(override=False)
    cli_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        init_sentry(entrypoint="cli")
    except ModuleNotFoundError as exc:
        if exc.name != "sentry_sdk" or not _is_update_invocation(cli_argv):
            raise
    install_questionary_escape_cancel()
    install_questionary_ctrl_c_double_exit()
    _install_sigint_handler()

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
            report_exception(exc, context="cli.main")
        exc.show()
        return exc.exit_code
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
            capture_exception(exc, context="cli.main.unhandled")
            with suppress(Exception):
                import sentry_sdk as _sentry_sdk

                _sentry_sdk.flush(timeout=2)
        raise
    finally:
        shutdown_analytics(flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
