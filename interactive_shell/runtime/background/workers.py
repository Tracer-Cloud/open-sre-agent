"""Background task lifecycle for the interactive REPL runtime."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from rich.console import Console

from core.domain.alerts import inbox as _alert_inbox
from interactive_shell.runtime.background.runner import drain_background_notices
from interactive_shell.runtime.core.state import ReplState, SpinnerState
from interactive_shell.session import ReplSession
from interactive_shell.ui.alerts import drain_and_render_incoming
from tools.fleet_monitoring.sampler import start_sampler

log = logging.getLogger(__name__)


class BackgroundTaskManager:
    """Start background workers and drain their user-visible output."""

    def __init__(
        self,
        session: ReplSession,
        state: ReplState,
        spinner: SpinnerState,
        inbox: _alert_inbox.AlertInbox | None,
        prompt_invalidator: Callable[[], None],
    ) -> None:
        self.session = session
        self.state = state
        self.spinner = spinner
        self.inbox = inbox
        self.prompt_invalidator = prompt_invalidator
        self.tasks: list[tuple[str, asyncio.Task[None]]] = []

    def start_all(
        self,
        processor_coro: Callable[[], Coroutine[Any, Any, None]],
    ) -> list[tuple[str, asyncio.Task[None]]]:
        self.tasks = [
            ("sampler", start_sampler()),
            ("processor", asyncio.create_task(processor_coro())),
            ("alert watcher", asyncio.create_task(self._alert_watcher())),
            ("spinner ticker", asyncio.create_task(self._spinner_ticker())),
        ]
        return self.tasks

    def drain_turn_start_output(self, console: Console) -> None:
        if self.inbox is not None:
            try:
                drain_and_render_incoming(self.session, console, self.inbox)
            except Exception as exc:
                log.warning("Error draining alerts at turn start: %s", exc)
        try:
            drain_background_notices(self.session, console)
        except Exception as exc:
            log.warning("Error draining background notices at turn start: %s", exc)

    async def _alert_watcher(self) -> None:
        if self.inbox is None:
            return
        alert_console = Console(
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        drain_and_render_incoming(self.session, alert_console, self.inbox)
        while not self.state.exit_requested:
            try:
                await asyncio.to_thread(self.inbox.pending_event.wait, timeout=1)
            except asyncio.CancelledError:
                return
            try:
                drain_and_render_incoming(self.session, alert_console, self.inbox)
            except Exception as exc:
                log.warning("Error draining incoming alerts: %s", exc)

    async def _spinner_ticker(self) -> None:
        # prompt_async's refresh_interval alone is not guaranteed to drive
        # visible prompt redraws while patch_stdout(raw=True) is active and
        # the LLM stream is writing rapidly. This task explicitly invalidates
        # the prompt at 100 ms intervals so the braille glyph cycles smoothly.
        tick_s = 0.1
        while not self.state.exit_requested:
            try:
                await asyncio.sleep(tick_s)
            except asyncio.CancelledError:
                return
            if self.spinner.streaming:
                self.prompt_invalidator()
