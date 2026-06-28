"""Interactive REPL for OpenSRE — Claude Code-style incident response terminal."""

from __future__ import annotations

from typing import Any


def run_repl(*args: Any, **kwargs: Any) -> Any:
    from interactive_shell.main import run_repl as runtime_run_repl

    return runtime_run_repl(*args, **kwargs)


__all__ = ["run_repl"]
