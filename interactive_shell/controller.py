"""Top-level interactive-shell controller: prompt input, dispatch, and shutdown."""

from __future__ import annotations

import asyncio
import logging

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from context.session import (
    ReplRuntimeContext,
    ReplSession,
    create_repl_runtime_context,
)
from core.domain.alerts import inbox as _alert_inbox
from interactive_shell.agent_shell.agent import (
    AgentTurnRunner,
    run_agent_turn_queue,
    run_input_loop,
)
from interactive_shell.runtime.background.workers import BackgroundTaskManager
from interactive_shell.runtime.core.prompt_manager import PromptManager
from interactive_shell.runtime.core.state import (
    ReplState,
    SpinnerState,
)
from interactive_shell.runtime.input import (
    PromptInputReader,
)
from interactive_shell.runtime.input.actions import (
    CancelTurn,
    CloseShell,
    DeliverConfirmation,
    IgnoreInput,
    InputAction,
    SubmitTurn,
)

log = logging.getLogger(__name__)


def _resolve_runtime_context(
    session: ReplSession | ReplRuntimeContext | None,
    *,
    state: ReplState | None,
    spinner: SpinnerState | None,
    pt_session: PromptSession[str] | None,
    inbox: _alert_inbox.AlertInbox | None,
) -> ReplRuntimeContext:
    if isinstance(session, ReplRuntimeContext):
        if state is None and spinner is None and pt_session is None and inbox is None:
            return session
        return ReplRuntimeContext(
            session=session.session,
            state=state if state is not None else session.state,
            spinner=spinner if spinner is not None else session.spinner,
            pt_session=pt_session if pt_session is not None else session.pt_session,
            inbox=inbox if inbox is not None else session.inbox,
        )
    return create_repl_runtime_context(
        session,
        state=state,
        spinner=spinner,
        pt_session=pt_session,
        inbox=inbox,
    )


class InteractiveShellController:
    """Coordinate prompt input, queued dispatch, background workers, and shutdown."""

    def __init__(
        self,
        session: ReplSession | ReplRuntimeContext | None = None,
        *,
        state: ReplState | None = None,
        spinner: SpinnerState | None = None,
        pt_session: PromptSession[str] | None = None,
        inbox: _alert_inbox.AlertInbox | None = None,
    ) -> None:
        self.runtime_context = _resolve_runtime_context(
            session,
            state=state,
            spinner=spinner,
            pt_session=pt_session,
            inbox=inbox,
        )
        self.session = self.runtime_context.session
        self.inbox = self.runtime_context.inbox
        self.state = self.runtime_context.state
        self.spinner = self.runtime_context.spinner
        self.prompt = PromptManager(
            self.session,
            self.state,
            self.spinner,
            self.runtime_context.pt_session,
        )
        self.turn_runner = AgentTurnRunner(
            session=self.session,
            state=self.state,
            spinner=self.spinner,
            invalidate_prompt=lambda: self.prompt.invalidate_prompt(),
        )
        self.echo_console = Console(highlight=False, force_terminal=True, color_system="truecolor")
        self.input_reader = PromptInputReader(
            self.prompt,
            self.state,
            self.session,
            self.echo_console,
        )
        self.background: BackgroundTaskManager | None = None
        self.tasks: list[tuple[str, asyncio.Task[None]]] = []

    async def start_interactive_shell(self) -> None:
        self.session.schedule_warm_resolved_integrations()
        self._start_runtime_services()
        try:
            with patch_stdout(raw=True):
                await run_input_loop(
                    state=self.state,
                    session=self.session,
                    background=self.background,
                    input_reader=self.input_reader,
                    echo_console=self.echo_console,
                    handle_input_action=self._handle_input_action,
                )
        finally:
            await self._shutdown_runtime()

    def _start_runtime_services(self) -> None:
        self.prompt.setup()
        self.background = BackgroundTaskManager(
            self.session,
            self.state,
            self.spinner,
            self.inbox,
            self.prompt.invalidate_prompt,
        )
        self.tasks = self.background.start_all(
            lambda: run_agent_turn_queue(
                state=self.state,
                run_turn=self.turn_runner.run_agent_turn,
            )
        )

    async def _handle_input_action(self, action: InputAction) -> bool:
        match action:
            case IgnoreInput():
                return True
            case CloseShell():
                return False
            case CancelTurn(submitted_text=text):
                if text:
                    self.prompt.render_submitted_prompt(self.echo_console, text)
                self.state.cancel_current_dispatch()
                return True
            case DeliverConfirmation(text=text):
                self.state.deliver_confirmation(text)
                return True
            case SubmitTurn(text=text, wait_until_idle=wait, warning=warning):
                if warning:
                    self.echo_console.print(warning)
                self.prompt.render_submitted_prompt(self.echo_console, text)
                await self.state.queue.put(text)
                if wait:
                    await self.state.queue.join()
                return True
        raise AssertionError(f"Unhandled input action: {action!r}")

    async def _shutdown_runtime(self) -> None:
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


__all__ = ["InteractiveShellController"]
