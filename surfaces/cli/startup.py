"""Everything the CLI process does once, before the Click group runs.

``main()`` is an entrypoint: read argv, invoke the group, map the outcome to an
exit code. Shared env boot lives in :func:`bootstrap.process.configure_process`;
this module adds CLI-only terminal hooks after that.

Order is load-bearing:

1. **shared process boot** (env via :data:`CLI_PROFILE`);
2. **error reporting** next, so a failure in any later step is still captured
   (CLI owns Sentry — ``update`` tolerates a missing SDK);
3. **observability adapters** before the group runs, so core code that calls the
   abstractions during a command routes through the Rich-aware implementations
   instead of the no-op defaults.
"""

from __future__ import annotations

from typing import Any

from surfaces.cli.invocation import resolve_command_parts

# Everything else is imported inside run(). Importing this module must stay
# cheap: the entry point imports it at module scope, and ``opensre --version``
# answers before run() is ever called. Pulling questionary/prompt_toolkit up
# here puts the whole terminal stack in front of that fast path.


def _first_command(group: Any, argv: list[str]) -> str:
    """The top-level command name in ``argv``, or "" when none resolves."""
    parts = resolve_command_parts(group, argv)
    return parts[0] if parts else ""


def sentry_entrypoint_for(group: Any, argv: list[str]) -> str:
    """Which entrypoint label error reports are tagged with.

    ``debug`` commands are separated so their noise does not mix with real CLI
    usage in error reporting.
    """
    return "debug" if _first_command(group, argv) == "debug" else "cli"


def _init_error_reporting(group: Any, argv: list[str], command: str) -> None:
    """Start error reporting, tolerating a missing SDK only during ``update``.

    ``opensre update`` replaces the installed tree, so ``sentry_sdk`` can be
    briefly absent mid-upgrade. Anywhere else its absence is a broken install and
    must surface.
    """
    from platform.observability.errors.sentry import init_sentry

    try:
        init_sentry(entrypoint=sentry_entrypoint_for(group, argv))
    except ModuleNotFoundError as exc:
        if exc.name != "sentry_sdk" or command != "update":
            raise


def run(group: Any, argv: list[str]) -> None:
    """Prepare this process for ``argv``. Safe to call once, before the group."""
    from bootstrap.process import CLI_PROFILE, configure_process
    from platform.terminal.prompt_support import (
        install_questionary_ctrl_c_double_exit,
        install_questionary_escape_cancel,
    )
    from surfaces.cli.signals import install_sigint_handler
    from surfaces.interactive_shell.ui.output.boundary import install_product_adapters

    command = _first_command(group, argv)
    configure_process(CLI_PROFILE)

    _init_error_reporting(group, argv, command)

    install_product_adapters()

    install_questionary_escape_cancel()
    install_questionary_ctrl_c_double_exit()
    install_sigint_handler()


__all__ = ["run", "sentry_entrypoint_for"]
