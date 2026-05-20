from __future__ import annotations

from typing import TYPE_CHECKING

from app.cli.interactive_shell.runtime.hot_reload import HotReloadCoordinator
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.tasks import (
    TaskKind,
    TaskRecord,
    TaskRegistry,
    TaskStatus,
)

if TYPE_CHECKING:
    from app.cli.interactive_shell.config import ReplConfig


async def repl_main(initial_input: str | None = None, _config: ReplConfig | None = None) -> int:
    """Lazily resolve the real REPL entrypoint to avoid import cycles."""
    from app.cli.interactive_shell.runtime.entrypoint import repl_main as _repl_main

    return await _repl_main(initial_input=initial_input, _config=_config)


def run_repl(initial_input: str | None = None, config: ReplConfig | None = None) -> int:
    """Lazily resolve the sync REPL entrypoint to avoid import cycles."""
    from app.cli.interactive_shell.runtime.entrypoint import run_repl as _run_repl

    return _run_repl(initial_input=initial_input, config=config)


__all__ = [
    "HotReloadCoordinator",
    "ReplSession",
    "TaskKind",
    "TaskRecord",
    "TaskRegistry",
    "TaskStatus",
    "repl_main",
    "run_repl",
]
