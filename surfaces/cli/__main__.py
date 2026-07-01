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
from collections.abc import Iterator, Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any, TypeVar, overload

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()

import click  # noqa: E402

from config.version import get_version  # noqa: E402

if TYPE_CHECKING:
    from platform.analytics.provider import Properties
    from platform.common.errors import OpenSREError

_CAPTURE_CLI_ANALYTICS = "capture_cli_analytics"
_CLI_ANALYTICS_CAPTURED = "cli_analytics_captured"
_CLI_ARGV = "cli_argv"
_GetDefault = TypeVar("_GetDefault")


class _ThemeParamType(click.ParamType):
    """Validate theme names without importing terminal UI dependencies at startup."""

    name = "theme"

    def _choices(self) -> tuple[str, ...]:
        from platform.terminal.theme import list_theme_names

        return list_theme_names()

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str:
        normalized = str(value).strip().lower()
        choices = self._choices()
        if normalized in choices:
            return normalized
        return self.fail(
            f"{value!r} is not one of: {', '.join(choices)}.",
            param,
            ctx,
        )


class _LazyCommandsDict(dict[str, click.Command]):
    """Click command mapping that loads the command tree on first read."""

    def __init__(self, owner: _LazyRichGroup, initial: Mapping[str, click.Command]) -> None:
        super().__init__(initial)
        self._owner = owner

    def _ensure(self) -> None:
        self._owner.ensure_commands_registered()

    def __contains__(self, key: object) -> bool:
        self._ensure()
        return super().__contains__(key)

    def __iter__(self) -> Iterator[str]:
        self._ensure()
        return super().__iter__()

    def __len__(self) -> int:
        self._ensure()
        return super().__len__()

    def __getitem__(self, key: str) -> click.Command:
        self._ensure()
        return super().__getitem__(key)

    @overload
    def get(self, key: str, default: None = None, /) -> click.Command | None:
        pass

    @overload
    def get(self, key: str, default: click.Command, /) -> click.Command:
        pass

    @overload
    def get(self, key: str, default: _GetDefault, /) -> click.Command | _GetDefault:
        pass

    def get(self, key: str, default: object = None, /) -> object:
        self._ensure()
        return super().get(key, default)

    def keys(self) -> Any:
        self._ensure()
        return super().keys()

    def values(self) -> Any:
        self._ensure()
        return super().values()

    def items(self) -> Any:
        self._ensure()
        return super().items()


class _LazyRichGroup(click.Group):
    """Root CLI group with lazy command registration and Rich help rendering."""

    _commands_registered: bool

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._commands_registered = False
        self.commands = _LazyCommandsDict(self, self.commands)

    def ensure_commands_registered(self) -> None:
        if self._commands_registered:
            return
        self._commands_registered = True
        from surfaces.cli.commands import register_commands

        register_commands(self)

    def list_commands(self, ctx: click.Context) -> list[str]:
        self.ensure_commands_registered()
        return super().list_commands(ctx)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        self.ensure_commands_registered()
        return super().get_command(ctx, cmd_name)

    def format_help(self, ctx: click.Context, _formatter: click.HelpFormatter) -> None:
        assert isinstance(ctx.command, click.Group)
        from surfaces.interactive_shell.ui.layout import render_help

        render_help(ctx.command)


def capture_first_run_if_needed() -> None:
    from platform.analytics.provider import capture_first_run_if_needed as _capture

    _capture()


def capture_cli_invoked(properties: Properties | None = None) -> None:
    from platform.analytics.cli import capture_cli_invoked as _capture

    _capture(properties)


def shutdown_analytics(*, flush: bool = True) -> None:
    from platform.analytics.provider import shutdown_analytics as _shutdown

    _shutdown(flush=flush)


def build_cli_invoked_properties(
    *,
    entrypoint: str,
    command_parts: list[str],
    json_output: bool,
    verbose: bool,
    debug: bool,
    yes: bool,
    interactive: bool,
) -> Properties:
    from platform.analytics.cli import build_cli_invoked_properties as _build

    return _build(
        entrypoint=entrypoint,
        command_parts=command_parts,
        json_output=json_output,
        verbose=verbose,
        debug=debug,
        yes=yes,
        interactive=interactive,
    )


def report_exception(exc: BaseException, *, context: str) -> None:
    from surfaces.interactive_shell.utils.error_handling.exception_reporting import (
        report_exception as _report_exception,
    )

    _report_exception(exc, context=context)


def should_report_exception(exc: click.ClickException) -> bool:
    from surfaces.interactive_shell.utils.error_handling.exception_reporting import (
        should_report_exception as _should_report_exception,
    )

    return _should_report_exception(exc)


def init_sentry(*, entrypoint: str | None = None) -> None:
    from platform.observability.sentry_sdk import init_sentry as _init_sentry

    _init_sentry(entrypoint=entrypoint)


def capture_exception(exc: BaseException, *, context: str) -> None:
    from platform.observability.sentry_sdk import capture_exception as _capture_exception

    _capture_exception(exc, context=context)


def render_landing(group: click.Group) -> None:
    from surfaces.interactive_shell.ui.layout import render_landing as _render_landing

    _render_landing(group)


def _load_structured_error_type() -> type[OpenSREError]:
    from platform.common.errors import OpenSREError

    return OpenSREError


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
    cls=_LazyRichGroup,
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
    type=_ThemeParamType(),
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
    command_parts = _resolve_command_parts(cli, argv)
    return bool(command_parts) and command_parts[0] == "update"


def _sentry_entrypoint_for_invocation(argv: list[str]) -> str:
    command_parts = _resolve_command_parts(cli, argv)
    if command_parts and command_parts[0] == "debug":
        return "debug"
    return "cli"


def _should_capture_cli_exception(exc: click.ClickException) -> bool:
    """Return whether a Click error represents an unexpected internal failure."""
    return should_report_exception(exc)


def _is_fast_version_invocation(argv: list[str]) -> bool:
    """Return whether argv can be answered before bootstrapping the full CLI."""
    return (
        argv == ["--version"]
        or argv == ["version"]
        or argv in (["--json", "version"], ["-j", "version"])
    )


def _print_fast_version(argv: list[str]) -> None:
    if argv == ["--version"]:
        click.echo(f"opensre, version {get_version()}")
        return

    import json

    import platform

    json_output = argv[0] in {"--json", "-j"}
    payload = {
        "opensre": get_version(),
        "python": platform.python_version(),
        "os": platform.system().lower(),
        "arch": platform.machine(),
    }
    if json_output:
        click.echo(json.dumps(payload))
        return
    click.echo(f"opensre {payload['opensre']}")
    click.echo(f"Python  {payload['python']}")
    click.echo(f"OS      {payload['os']} ({payload['arch']})")


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``opensre`` console script."""
    _ensure_utf8_stdio()
    cli_argv = list(sys.argv[1:] if argv is None else argv)
    if _is_fast_version_invocation(cli_argv):
        _print_fast_version(cli_argv)
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
    StructuredError = _load_structured_error_type()

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
        # a ClickException, so render it here the same way the CLI subclass'
        # show() does (clean panel, no traceback) and exit with its code.
        from rich.console import Console

        from platform.terminal.errors import render_error

        hint: str | None = None
        if exc.suggestion:
            parts = [exc.suggestion]
            if exc.docs_url:
                parts.append(f"Docs: {exc.docs_url}")
            hint = "  ".join(parts)
        render_error(exc, console=Console(stderr=True, highlight=False), hint=hint)
        return int(exc.exit_code)
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
