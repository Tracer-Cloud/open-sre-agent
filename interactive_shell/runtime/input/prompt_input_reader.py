"""Convert prompt-toolkit terminal behavior into shell input events."""

from __future__ import annotations

from rich.console import Console

from interactive_shell.runtime.core.prompt_manager import PromptManager
from interactive_shell.runtime.core.state import ReplState
from interactive_shell.runtime.input.events import (
    InputCancelled,
    InputClosed,
    InputEvent,
    InputSubmitted,
)
from interactive_shell.session import ReplSession
from interactive_shell.ui.components.cpr_stdin import (
    contains_cpr_sequence,
    strip_cpr_sequences,
)
from platform.terminal.prompt_support import (
    repl_prompt_note_ctrl_c,
    repl_reset_ctrl_c_gate,
)


class PromptInputReader:
    """Read prompt text and hide terminal-specific control flow from the loop."""

    def __init__(
        self,
        prompt: PromptManager,
        state: ReplState,
        session: ReplSession,
        console: Console,
    ) -> None:
        self.prompt = prompt
        self.state = state
        self.session = session
        self.console = console

    async def read(self) -> InputEvent:
        while True:
            try:
                text = await self.prompt.read_prompt_text()
            except EOFError:
                if self.state.is_dispatch_running():
                    return InputCancelled()
                self._render_session_resume_hint()
                return InputClosed()
            except KeyboardInterrupt:
                if self.state.is_dispatch_running():
                    return InputCancelled()
                if repl_prompt_note_ctrl_c(self.console, self.session.session_id):
                    return InputClosed()
                return InputCancelled()

            repl_reset_ctrl_c_gate()
            raw_text = text
            text = strip_cpr_sequences(text)
            if not text.strip() and contains_cpr_sequence(raw_text):
                continue
            return InputSubmitted(text)

    def _render_session_resume_hint(self) -> None:
        if not self.session.session_id:
            return
        self.console.print()
        self.console.print("Resume this session with:")
        self.console.print(f"/resume {self.session.session_id}")
        self.console.print("Goodbye!")


__all__ = ["PromptInputReader"]
