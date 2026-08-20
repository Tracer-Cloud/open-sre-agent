"""Interactive REPL for OpenSRE — Claude Code-style incident response terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from surfaces.interactive_shell.ensure_platform import ensure_interactive_shell_platform

if TYPE_CHECKING:
    import click
    from rich.console import Console

    from config.repl_config import ReplConfig


def run_repl(
    initial_input: str | None = None,
    config: ReplConfig | None = None,
    *,
    resume_session_id: str | None = None,
    console: Console | None = None,
    cli_command_group: click.Command | None = None,
) -> int:
    """Run the interactive shell and return its exit code.

    Mirrors :func:`surfaces.interactive_shell.main.run_repl`. Importing that
    module pulls in the whole terminal stack, so the import stays inside the
    call — the signature is repeated here rather than erased to ``*args`` so
    callers keep type checking. Repeating it means every runtime parameter has
    to be repeated too: one omitted here is unreachable through this facade,
    which is the seam embedding callers import.
    """
    from surfaces.interactive_shell.main import run_repl as runtime_run_repl

    return runtime_run_repl(
        initial_input=initial_input,
        config=config,
        resume_session_id=resume_session_id,
        console=console,
        cli_command_group=cli_command_group,
    )


# First-party ``platform`` must win over the stdlib module before any submodule
# under this package runs ``from platform… import`` at import time. Doing it once
# here — with nothing imported afterwards — covers the whole package tree, so no
# submodule needs its own ensure. ``ensure_platform`` holds the call-time
# counterpart used at entrypoints after an embedding host re-caches ``platform``.
ensure_interactive_shell_platform()

__all__ = ["run_repl"]
