"""Interactive REPL for OpenSRE — Claude Code-style incident response terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.repl_config import ReplConfig


def run_repl(
    initial_input: str | None = None,
    config: ReplConfig | None = None,
    *,
    resume_session_id: str | None = None,
) -> int:
    """Run the interactive shell and return its exit code.

    Mirrors :func:`surfaces.interactive_shell.main.run_repl`. Importing that
    module pulls in the whole terminal stack, so the import stays inside the
    call — the signature is repeated here rather than erased to ``*args`` so
    callers keep type checking.
    """
    from surfaces.interactive_shell.main import run_repl as runtime_run_repl

    return runtime_run_repl(
        initial_input=initial_input,
        config=config,
        resume_session_id=resume_session_id,
    )


__all__ = ["run_repl"]
