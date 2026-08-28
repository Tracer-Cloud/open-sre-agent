"""Prompt lifecycle and rendering glue for the interactive REPL loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console

from surfaces.interactive_shell.runtime.core.state import (
    PROMPT_REFRESH_INTERVAL_S,
    ReplState,
    SpinnerState,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import input_prompt
from surfaces.interactive_shell.ui.input_prompt import rendering as prompt_rendering
from surfaces.interactive_shell.ui.input_prompt.key_bindings import (
    build_cancel_key_bindings,
    install_session_key_bindings,
)
from surfaces.interactive_shell.ui.input_prompt.refresh import wire_prompt_refresh
from surfaces.interactive_shell.ui.input_prompt.style import refresh_prompt_theme
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region
from surfaces.shared.terminal.components.cpr_stdin import drain_stale_cpr_bytes

# Brief pause so a CPR reply still in flight lands in the stdin buffer before the
# non-blocking drain runs; without it the reply leaks into this prompt as literal bytes.
_CPR_SETTLE_SECONDS = 0.05


class PromptBuilder:
    """Own prompt-toolkit setup, prompt rendering, and prompt redraw hooks."""

    def __init__(
        self,
        session: Session,
        state: ReplState,
        spinner: SpinnerState,
        pt_session: PromptSession[str] | None = None,
    ) -> None:
        self.session = session
        self.state = state
        self.spinner = spinner
        self.pt_session = pt_session
        self.pt_app: Application[str] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._invalidate_prompt: Callable[[], None] | None = None
        self._submitted: asyncio.Queue[str] = asyncio.Queue()
        self._prompt_task: asyncio.Task[str] | None = None

    def setup(self) -> None:
        if self.pt_session is None:
            self.pt_session = input_prompt.build_prompt_session(self.session)
            self.session.terminal.prompt_history_backend = self.pt_session.history

        cancel_kb = build_cancel_key_bindings(self.state)
        install_session_key_bindings(self.pt_session, cancel_kb)

        self.pt_app = self.pt_session.app
        self.pt_session.default_buffer.accept_handler = self._accept_prompt_buffer
        self.loop = asyncio.get_running_loop()
        self.session.terminal.prompt_app = self.pt_app
        self.session.terminal.main_loop = self.loop
        self.state.bind_loop(self.loop)
        self._invalidate_prompt = wire_prompt_refresh(self.session, self.pt_app, self.loop)

    @property
    def invalidate_prompt(self) -> Callable[[], None]:
        if self._invalidate_prompt is None:
            raise RuntimeError("PromptBuilder.setup() must run before prompt invalidation")
        return self._invalidate_prompt

    def request_exit(self) -> None:
        if self.pt_app is None or self.loop is None:
            self.state.request_exit()
            return

        self.state.request_exit()

        def _exit_prompt_app(attempts_left: int = 5) -> None:
            if self.pt_app is not None and self.pt_app.is_running:
                self.pt_app.exit(result="")
                return
            if attempts_left > 0 and self.loop is not None:
                self.loop.call_later(0.02, _exit_prompt_app, attempts_left - 1)

        self.loop.call_soon_threadsafe(_exit_prompt_app)

    def message_with_spinner(self) -> ANSI:
        return render_prompt_region(self.session, self.state, self.spinner)

    def _accept_prompt_buffer(self, buffer: Buffer) -> bool:
        """Queue accepted text while keeping the prompt application alive."""
        self._submitted.put_nowait(buffer.text)
        return False

    def _start_prompt_if_needed(self) -> asyncio.Task[str]:
        if self.pt_session is None:
            raise RuntimeError("PromptBuilder.setup() must run before reading prompts")
        task = self._prompt_task
        if task is None:
            task = asyncio.create_task(
                self.pt_session.prompt_async(
                    message=self.message_with_spinner,
                    bottom_toolbar=self.spinner.toolbar_ansi,
                    refresh_interval=PROMPT_REFRESH_INTERVAL_S,
                    placeholder=lambda: prompt_rendering.resolve_prompt_placeholder(self.session),
                )
            )
            self._prompt_task = task
        return task

    async def suspend(self) -> None:
        """Release stdin while an exclusive picker or wizard is running."""
        task = self._prompt_task
        if task is None:
            return
        if not task.done() and self.pt_app is not None and self.pt_app.is_running:
            self.pt_app.exit(result="")
        await asyncio.gather(task, return_exceptions=True)
        if self._prompt_task is task:
            self._prompt_task = None

    async def close(self) -> None:
        """Stop the persistent prompt application during shell shutdown."""
        task = self._prompt_task
        self._prompt_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def read_prompt_text(self) -> str:
        if self.pt_session is None:
            raise RuntimeError("PromptBuilder.setup() must run before reading prompts")

        if self.session.terminal.pending_theme_refresh:
            self.session.terminal.pending_theme_refresh = False
            refresh_prompt_theme(self.session)
        await asyncio.sleep(_CPR_SETTLE_SECONDS)
        drain_stale_cpr_bytes()

        prefilled = self.session.terminal.pop_pending_prompt_default()
        if prefilled and self.session.terminal.pop_pending_autosubmit():
            # Same paint path as Enter: mark so ``render_submitted_prompt`` can
            # label ``/goal`` work turns distinctly from the ``/goal set`` slash.
            self.session.terminal.last_input_autosubmitted = True
            return prefilled

        if prefilled:
            self.pt_session.default_buffer.text = prefilled

        prompt_task = self._start_prompt_if_needed()
        submitted = asyncio.create_task(self._submitted.get())
        try:
            done, _pending = await asyncio.wait(
                {prompt_task, submitted},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if prompt_task in done:
                submitted.cancel()
                await asyncio.gather(submitted, return_exceptions=True)
                self._prompt_task = None
                return await prompt_task
            return submitted.result()
        except BaseException:
            submitted.cancel()
            await asyncio.gather(submitted, return_exceptions=True)
            raise

    def render_submitted_prompt(self, console: Console, text: str) -> None:
        prompt_rendering.render_submitted_prompt(console, self.session, text)
