"""Interactive shell runtime controller."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

from core.domain.alerts import inbox as _alert_inbox
from interactive_shell.harness.pipeline import handle_message_with_agent
from interactive_shell.runtime.background.workers import BackgroundTaskManager
from interactive_shell.runtime.core.context import (
    ReplRuntimeContext,
    create_repl_runtime_context,
)
from interactive_shell.runtime.core.prompt_manager import PromptManager
from interactive_shell.runtime.core.session import ReplSession
from interactive_shell.runtime.core.state import (
    PROMPT_REFRESH_INTERVAL_S,
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
    ShellInputSnapshot,
    SubmitTurn,
    decide_input_action,
)
from interactive_shell.runtime.utils.input_policy import (
    turn_needs_exclusive_stdin,
    turn_should_show_spinner,
)
from interactive_shell.ui import ERROR, WARNING
from interactive_shell.ui.components.cpr_stdin import drain_stale_cpr_bytes
from interactive_shell.ui.output.repl_progress import repl_safe_progress_scope
from interactive_shell.ui.streaming.console import StreamingConsole
from interactive_shell.utils.error_handling.exception_reporting import report_exception
from interactive_shell.utils.telemetry import PromptRecorder
from platform.analytics.repl_context import bind_cli_session_id, reset_cli_session_id

log = logging.getLogger(__name__)

_AGENT_TURN_KIND = "agent"


class DispatchCancelled(Exception):
    """Raised when in-flight dispatch is cancelled during confirmation."""


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
        self.runtime_context = self._resolve_runtime_context(
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
        self.echo_console = Console(highlight=False, force_terminal=True, color_system="truecolor")
        self.input_reader = PromptInputReader(
            self.prompt,
            self.state,
            self.session,
            self.echo_console,
        )
        self.background: BackgroundTaskManager | None = None
        self.tasks: list[tuple[str, asyncio.Task[None]]] = []

    def _resolve_runtime_context(
        self,
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
                state=state or session.state,
                spinner=spinner or session.spinner,
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

    async def start_interactive_shell(self) -> None:
        self.session.schedule_warm_resolved_integrations()
        self._start_runtime_services()
        try:
            with patch_stdout(raw=True):
                await self._run_prompt_loop()
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
        self.tasks = self.background.start_all(self._run_turn_queue_loop)

    async def _run_prompt_loop(self) -> None:
        while not self.state.exit_requested:
            if self.background is not None:
                self.background.drain_turn_start_output(self.echo_console)
            event = await self.input_reader.read()
            action = decide_input_action(
                event,
                ShellInputSnapshot(
                    exit_requested=self.state.exit_requested,
                    dispatch_running=self.state.is_dispatch_running(),
                    awaiting_confirmation=self.state.is_awaiting_confirmation(),
                ),
                needs_exclusive_stdin=lambda text: turn_needs_exclusive_stdin(
                    text,
                    self.session,
                ),
            )
            should_continue = await self._handle_input_action(action)
            if not should_continue:
                return

    async def _handle_input_action(self, action: InputAction) -> bool:
        match action:
            case IgnoreInput():
                return True
            case CloseShell():
                return False
            case CancelTurn(submitted_text=text):
                if text:
                    self.prompt.render_submitted_prompt(self.echo_console, text)
                self._cancel_current_turn()
                return True
            case DeliverConfirmation(text=text):
                self.state.deliver_confirmation(text)
                return True
            case SubmitTurn(text=text, wait_until_idle=wait, warning=warning):
                if warning:
                    self.echo_console.print(warning)
                self.prompt.render_submitted_prompt(self.echo_console, text)
                await self._enqueue_turn(text)
                if wait:
                    await self._await_turn_completion()
                return True

    async def _enqueue_turn(self, text: str) -> None:
        await self.state.queue.put(text)

    async def _await_turn_completion(self) -> None:
        await self.state.queue.join()

    def _cancel_current_turn(self) -> None:
        self.state.cancel_current_dispatch()

    async def _run_turn_queue_loop(self) -> None:
        while not self.state.exit_requested:
            try:
                text = await self.state.queue.get()
            except asyncio.CancelledError:
                return
            if self.state.exit_requested:
                self.state.queue.task_done()
                return

            turn_task = asyncio.create_task(self._run_queued_turn(text))
            self.state.current_task = turn_task
            try:
                await turn_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.debug("Queued turn task ended with exception: %s", exc)
            self.state.clear_current_task()
            self.state.queue.task_done()

    async def _run_queued_turn(self, text: str) -> None:
        dispatch_cancel = threading.Event()
        current_task = asyncio.current_task()
        if current_task is not None:
            self.state.start_dispatch(task=current_task, cancel_event=dispatch_cancel)
        else:
            self.state.current_cancel_event = dispatch_cancel

        console = StreamingConsole(
            self.spinner,
            dispatch_cancel,
            prompt_invalidator=self.prompt.invalidate_prompt,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        from interactive_shell.ui.output import set_prompt_suppress_fn

        show_spinner = turn_should_show_spinner(text, self.session)
        if show_spinner:
            self.spinner.start()
            set_prompt_suppress_fn(console.suppress_prompt_spinner)
        try:
            progress_scope = (
                contextlib.nullcontext()
                if turn_needs_exclusive_stdin(text, self.session)
                else repl_safe_progress_scope()
            )
            session_token = bind_cli_session_id(self.session.session_id)
            try:
                recorder = PromptRecorder.start(
                    session=self.session,
                    text=text,
                    turn_kind=_AGENT_TURN_KIND,
                )
                with progress_scope:
                    await asyncio.to_thread(
                        handle_message_with_agent,
                        text,
                        self.session,
                        console,
                        recorder=recorder,
                        confirm_fn=lambda prompt: request_confirmation_via_prompt(
                            self.state,
                            prompt,
                        ),
                        is_tty=None,
                    )
            finally:
                reset_cli_session_id(session_token)
        except asyncio.CancelledError:
            console.print(f"[{WARNING}]· interrupted[/]")
            raise
        except DispatchCancelled:
            console.print(f"[{WARNING}]· interrupted[/]")
        except Exception as exc:
            report_exception(exc, context="interactive_shell.turn")
            console.print(f"[{ERROR}]turn error:[/] {escape(str(exc))}")
        finally:
            set_prompt_suppress_fn(None)
            if show_spinner:
                self.spinner.stop()
            self.state.finish_dispatch(dispatch_cancel)
            await asyncio.sleep(0.05)
            drain_stale_cpr_bytes()

    async def _shutdown_runtime(self) -> None:
        self.state.request_exit()
        self._cancel_current_turn()

        for _label, task in self.tasks:
            task.cancel()

        shutdown_results = await asyncio.gather(
            *(task for _label, task in self.tasks),
            return_exceptions=True,
        )
        for (label, _task), result in zip(self.tasks, shutdown_results, strict=True):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                log.debug("%s task shutdown raised exception: %s", label, result)


def request_confirmation_via_prompt(state: ReplState, prompt_text: str) -> str:
    response_event = threading.Event()
    state.begin_confirmation(response_event, prompt_text)
    try:
        while not response_event.is_set():
            cancel = state.current_cancel_event
            if cancel is not None and cancel.is_set():
                raise DispatchCancelled("cancelled while awaiting confirmation")
            response_event.wait(timeout=PROMPT_REFRESH_INTERVAL_S)
        if not state.confirm_response:
            raise DispatchCancelled("cancelled while awaiting confirmation")
        return state.confirm_response[0]
    finally:
        state.clear_confirmation()


__all__ = [
    "DispatchCancelled",
    "InteractiveShellController",
    "request_confirmation_via_prompt",
]
