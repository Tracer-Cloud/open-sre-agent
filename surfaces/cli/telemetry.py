"""Lazy platform shims for CLI analytics, Sentry, error reporting, and landing UI.

Each function defers its heavy ``platform``/UI import until called, keeping CLI
startup cheap. ``surfaces.cli.__main__`` imports these names, so tests patch the
seam there (e.g. ``surfaces.cli.__main__.capture_cli_invoked``) and the
entrypoint's own callers pick up the patched global.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from platform.analytics.provider import Properties
    from platform.common.errors import OpenSREError


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


def load_structured_error_type() -> type[OpenSREError]:
    from platform.common.errors import OpenSREError

    return OpenSREError


def render_structured_error(exc: OpenSREError) -> int:
    """Render a structured ``OpenSREError`` as a clean panel and return its exit code.

    Used for structured errors raised by non-CLI code (tools/integrations) that
    are not ``ClickException`` instances, so Click never renders them itself.
    """
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
