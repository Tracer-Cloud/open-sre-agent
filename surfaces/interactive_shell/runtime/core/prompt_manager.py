"""Prompt lifecycle and rendering glue for the interactive REPL loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console

from surfaces.interactive_shell.runtime.core.state import (
    PROMPT_REFRESH_INTERVAL_S,
    ReplState,
    SpinnerState,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import input_prompt
from surfaces.interactive_shell.ui.components.cpr_stdin import (
    drain_stale_cpr_bytes,
    strip_cpr_sequences,
)
from surfaces.interactive_shell.ui.input_prompt import rendering as prompt_rendering
from surfaces.interactive_shell.ui.input_prompt.key_bindings import (
    build_cancel_key_bindings,
    install_session_key_bindings,
)
from surfaces.interactive_shell.ui.input_prompt.refresh import wire_prompt_refresh
from surfaces.interactive_shell.ui.input_prompt.style import refresh_prompt_theme

# Brief pause so a CPR reply still in flight lands in the stdin buffer before the
# non-blocking drain runs; without it the reply leaks into this prompt as literal bytes.
_CPR_SETTLE_SECONDS = 0.05


class PromptManager:
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

    def setup(self) -> None:
        if self.pt_session is None:
            self.pt_session = input_prompt.build_prompt_session(self.session)
            self.session.terminal.prompt_history_backend = self.pt_session.history

        cancel_kb = build_cancel_key_bindings(self.state)
        install_session_key_bindings(self.pt_session, cancel_kb)

        self.pt_app = self.pt_session.app
        self.loop = asyncio.get_running_loop()
        self.session.terminal.prompt_app = self.pt_app
        self.session.terminal.main_loop = self.loop
        self.state.bind_loop(self.loop)
        self._invalidate_prompt = wire_prompt_refresh(self.session, self.pt_app, self.loop)

    @property
    def invalidate_prompt(self) -> Callable[[], None]:
        if self._invalidate_prompt is None:
            raise RuntimeError("PromptManager.setup() must run before prompt invalidation")
        return self._invalidate_prompt

    def request_exit(self) -> None:
        if self.pt_app is None or self.loop is None:
            self.state.request_exit()
            return

        self.state.request_exit()

        def _exit_prompt_app(attempts_left: int = 5) -> None:
            if self.pt_app is not None and self.pt_app.is_running:
                self.pt_app.exit()
                return
            if attempts_left > 0 and self.loop is not None:
                self.loop.call_later(0.02, _exit_prompt_app, attempts_left - 1)

        self.loop.call_soon_threadsafe(_exit_prompt_app)

    def message_with_spinner(self) -> ANSI:
        base = prompt_rendering._prompt_message(self.session).value
        if self.state.is_awaiting_confirmation():
            confirm_text = self.state.confirm_prompt_text
            return ANSI(f"{confirm_text}\n{base}")
        prefix = strip_cpr_sequences(
            prompt_rendering.resolve_prompt_prefix_ansi(
                inline_spinner=self.spinner.inline_spinner_ansi(),
                idle_hint=prompt_rendering.resolve_idle_hint_ansi(self.session),
            )
        )
        return ANSI(f"{prefix}\n{base}")

    async def read_prompt_text(self) -> str:
        if self.pt_session is None:
            raise RuntimeError("PromptManager.setup() must run before reading prompts")

        if self.session.terminal.pending_theme_refresh:
            self.session.terminal.pending_theme_refresh = False
            refresh_prompt_theme(self.session)
        await asyncio.sleep(_CPR_SETTLE_SECONDS)
        drain_stale_cpr_bytes()

        prefilled = self.session.terminal.pop_pending_prompt_default()
        if prefilled and self.session.terminal.pop_pending_autosubmit():
            return prefilled

        return await self.pt_session.prompt_async(
            message=self.message_with_spinner,
            bottom_toolbar=self.spinner.toolbar_ansi,
            refresh_interval=PROMPT_REFRESH_INTERVAL_S,
            placeholder=lambda: prompt_rendering.resolve_prompt_placeholder(self.session),
            default=prefilled,
        )

    def render_submitted_prompt(self, console: Console, text: str) -> None:
        prompt_rendering.render_submitted_prompt(console, self.session, text)
