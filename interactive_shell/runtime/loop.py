"""Interactive prompt loop orchestration for the interactive shell."""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from core.domain.alerts import inbox as _alert_inbox
from interactive_shell.runtime.background.workers import BackgroundTaskManager
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.runtime.core.state import ReplState, SpinnerState
from interactive_shell.runtime.dispatch import (
    dispatch_needs_exclusive_stdin,
    looks_like_cancel_request,
    looks_like_confirmation_answer,
)
from interactive_shell.runtime.dispatch_processor import DispatchProcessor
from interactive_shell.runtime.prompt_manager import PromptManager
from interactive_shell.runtime.shutdown import ShutdownHandler
from interactive_shell.ui.components.cpr_stdin import contains_cpr_sequence, strip_cpr_sequences
from platform.terminal.prompt_support import (
    repl_prompt_note_ctrl_c,
    repl_reset_ctrl_c_gate,
)


class InteractiveShellLoop:
    """Coordinate prompt input, queued dispatch, background workers, and shutdown."""

    def __init__(
        self,
        session: ReplSession,
        *,
        pt_session: PromptSession[str] | None = None,
        inbox: _alert_inbox.AlertInbox | None = None,
    ) -> None:
        self.session = session
        self.inbox = inbox
        self.state = ReplState()
        self.spinner = SpinnerState()
        self.prompt = PromptManager(session, self.state, self.spinner, pt_session)
        self.dispatch_processor: DispatchProcessor | None = None
        self.background: BackgroundTaskManager | None = None
        self.shutdown_handler: ShutdownHandler | None = None

    async def run(self) -> None:
        self.session.schedule_warm_resolved_integrations()
        self._setup()
        try:
            with patch_stdout(raw=True):
                await self._main_loop()
        finally:
            if self.shutdown_handler is not None:
                await self.shutdown_handler.shutdown()

    def _setup(self) -> None:
        self.prompt.setup()
        self.dispatch_processor = DispatchProcessor(
            self.session,
            self.state,
            self.spinner,
            prompt_invalidator=self.prompt.invalidate_prompt,
            on_exit=self.prompt.request_exit,
        )
        self.background = BackgroundTaskManager(
            self.session,
            self.state,
            self.spinner,
            self.inbox,
            self.prompt.invalidate_prompt,
        )
        tasks = self.background.start_all(self.dispatch_processor.run_queue)
        self.shutdown_handler = ShutdownHandler(self.state, tasks)

    async def _main_loop(self) -> None:
        echo_console = Console(highlight=False, force_terminal=True, color_system="truecolor")
        while True:
            if self.state.exit_requested:
                return
            self._drain_turn_start_output(echo_console)
            text = await self._read_next_input(echo_console)
            if text is None:
                return
            if await self._handle_turn_text(text, echo_console):
                continue

    def _drain_turn_start_output(self, console: Console) -> None:
        if self.background is None:
            return
        self.background.drain_turn_start_output(console)

    async def _read_next_input(self, console: Console) -> str | None:
        try:
            text = await self.prompt.read_prompt_text()
        except EOFError:
            if self.state.is_dispatch_running():
                self.state.cancel_current_dispatch()
                return ""
            self._render_session_resume_hint(console)
            return None
        except KeyboardInterrupt:
            if self.state.is_dispatch_running():
                self.state.cancel_current_dispatch()
                return ""
            if repl_prompt_note_ctrl_c(console, self.session.session_id):
                return None
            return ""

        repl_reset_ctrl_c_gate()
        raw_text = text
        text = strip_cpr_sequences(text)
        if not text.strip() and contains_cpr_sequence(raw_text):
            return ""
        return text

    def _render_session_resume_hint(self, console: Console) -> None:
        if not self.session.session_id:
            return
        console.print()
        console.print("Resume this session with:")
        console.print(f"/resume {self.session.session_id}")
        console.print("Goodbye!")

    async def _handle_turn_text(self, text: str, console: Console) -> bool:
        if self.state.exit_requested:
            return False
        if not text:
            return True
        if self._handle_cancel_request(text, console):
            return True
        if await self._handle_confirmation_or_queue(text, console):
            return True
        return await self._queue_regular_turn(text, console)

    def _handle_cancel_request(self, text: str, console: Console) -> bool:
        if not (self.state.is_dispatch_running() and looks_like_cancel_request(text)):
            return False
        stripped = (text or "").strip()
        self.prompt.render_submitted_prompt(console, stripped)
        self.state.cancel_current_dispatch()
        return True

    async def _handle_confirmation_or_queue(self, text: str, console: Console) -> bool:
        if not self.state.is_awaiting_confirmation():
            return False
        if looks_like_confirmation_answer(text):
            self.state.deliver_confirmation(text or "")
            return True
        console.print(
            "[dim](type y/N to confirm the pending action; your input has been queued for after)[/]"
        )
        stripped = (text or "").strip()
        if stripped:
            self.prompt.render_submitted_prompt(console, stripped)
            await self.state.queue.put(stripped)
        return True

    async def _queue_regular_turn(self, text: str, console: Console) -> bool:
        stripped = (text or "").strip()
        if not stripped:
            return True
        self.prompt.render_submitted_prompt(console, stripped)
        wait_for_dispatch = dispatch_needs_exclusive_stdin(stripped, self.session)
        await self.state.queue.put(stripped)
        if wait_for_dispatch:
            await self.state.queue.join()
        return True


async def run_interactive(
    session: ReplSession,
    pt_session: PromptSession[str] | None = None,
    inbox: _alert_inbox.AlertInbox | None = None,
) -> None:
    loop = InteractiveShellLoop(session, pt_session=pt_session, inbox=inbox)
    await loop.run()


__all__ = ["InteractiveShellLoop", "run_interactive"]
