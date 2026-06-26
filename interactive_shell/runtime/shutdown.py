"""Clean shutdown coordination for the interactive REPL runtime."""

from __future__ import annotations

import asyncio
import logging

from interactive_shell.runtime.core.state import ReplState

log = logging.getLogger(__name__)


class ShutdownHandler:
    """Cancel runtime workers and surface shutdown exceptions at debug level."""

    def __init__(
        self,
        state: ReplState,
        tasks: list[tuple[str, asyncio.Task[None]]],
    ) -> None:
        self.state = state
        self.tasks = tasks

    async def shutdown(self) -> None:
        self.state.request_exit()
        self.state.cancel_current_dispatch()

        for _label, task in self.tasks:
            task.cancel()

        shutdown_results = await asyncio.gather(
            *(task for _label, task in self.tasks),
            return_exceptions=True,
        )
        for (label, _task), result in zip(self.tasks, shutdown_results, strict=True):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                log.debug("%s task shutdown raised exception: %s", label, result)
