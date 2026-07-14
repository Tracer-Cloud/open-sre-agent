"""Runtime turn host for submitted interactive-shell prompts.

Three public runtime functions live here:

- ``run_agent_turn`` — set up shell presentation for one submitted turn and drive
  its lifecycle (the injected ``run_turn`` callable for the queue).
- ``run_input_loop`` — read prompt input events and dispatch them until exit.
- ``run_agent_turn_queue`` — consume queued turns and run each one until exit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from platform.analytics.repl_context import bound_repl_turn_context
from platform.observability.trace.spans import bind_session_trace, emit_thread_boundary
from surfaces.interactive_shell.runtime.agent_presentation import (
    AgentEvent,
    AgentEventSink,
    ConsoleAgentEventSink,
)
from surfaces.interactive_shell.runtime.background.workers import BackgroundTaskManager
from surfaces.interactive_shell.runtime.core.confirmation import (
    DispatchCancelled,
    request_confirmation_via_prompt,
)
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.runtime.input import PromptInputReader
from surfaces.interactive_shell.runtime.input.actions import (
    InputAction,
    ShellInputSnapshot,
    decide_input_action,
)
from surfaces.interactive_shell.runtime.utils.input_policy import (
    turn_needs_exclusive_stdin,
)
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.output.console_state import set_investigation_spinner
from surfaces.interactive_shell.ui.output.repl_progress import repl_safe_progress_scope
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole
from surfaces.interactive_shell.utils.error_handling.exception_reporting import report_exception
from surfaces.interactive_shell.utils.telemetry import PromptRecorder

_logger = logging.getLogger(__name__)

_AGENT_TURN_KIND = "agent"


@dataclass(frozen=True)
class AgentTurnRuntime:
    """Immutable dependencies for running one submitted shell turn."""

    session: Session
    state: ReplState
    spinner: SpinnerState
    invalidate_prompt: Callable[[], None]
    request_exit: Callable[[], None] | None = None


async def run_agent_turn(runtime: AgentTurnRuntime, text: str) -> None:
    """Set up shell presentation for one turn and drive its lifecycle."""
    dispatch_cancel = threading.Event()
    console = StreamingConsole(
        runtime.spinner,
        dispatch_cancel,
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    emit = ConsoleAgentEventSink(
        session=runtime.session,
        spinner=runtime.spinner,
        console=console,
    )
    recorder = PromptRecorder.start(
        session=runtime.session,
        text=text,
        turn_kind=_AGENT_TURN_KIND,
    )
    exclusive_stdin = turn_needs_exclusive_stdin(text, runtime.session)
    progress_scope = contextlib.nullcontext() if exclusive_stdin else repl_safe_progress_scope()
    runtime.session.terminal.exclusive_stdin_active = exclusive_stdin
    # Expose this turn's spinner so investigation stages can animate phase labels.
    set_investigation_spinner(runtime.spinner)
    emit_thread_boundary(
        runtime.session.session_id,
        name="turn_boundary",
        phase="turn_start",
    )
    try:
        with (
            bind_session_trace(runtime.session.session_id),
            progress_scope,
        ):
            await _run_agent_turn_loop(
                runtime=runtime,
                text=text,
                output=console,
                recorder=recorder,
                confirm=lambda prompt: request_confirmation_via_prompt(runtime.state, prompt),
                emit=emit,
                dispatch_cancel=dispatch_cancel,
            )
    finally:
        set_investigation_spinner(None)
        runtime.session.terminal.exclusive_stdin_active = False
        emit_thread_boundary(
            runtime.session.session_id,
            name="turn_boundary",
            phase="turn_end",
        )


async def _run_agent_turn_loop(
    *,
    runtime: AgentTurnRuntime,
    text: str,
    output: StreamingConsole,
    recorder: PromptRecorder | None,
    confirm: Callable[[str], str],
    emit: AgentEventSink,
    dispatch_cancel: threading.Event,
) -> None:
    current_task = asyncio.current_task()
    if current_task is not None:
        runtime.state.start_dispatch(task=current_task, cancel_event=dispatch_cancel)
    else:
        runtime.state.attach_cancel_event(dispatch_cancel)

    await emit(AgentEvent(type="turn_start", text=text))
    try:
        # Imported lazily so constructing the controller (and importing this
        # module) does not pull the harness/turn-execution stack
        # (``action_agent -> core.agent``) before the first turn is queued.
        from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn

        with bound_repl_turn_context(
            session_id=runtime.session.session_id,
            turn_kind=_AGENT_TURN_KIND,
            prompt_turn_id=recorder.turn_id if recorder is not None else None,
        ):
            await asyncio.to_thread(
                execute_shell_turn,
                text,
                runtime.session,
                output,
                recorder=recorder,
                confirm_fn=confirm,
                is_tty=None,
                request_exit=runtime.request_exit,
            )
    except asyncio.CancelledError:
        await emit(AgentEvent(type="turn_interrupted"))
        raise
    except DispatchCancelled:
        await emit(AgentEvent(type="turn_interrupted"))
    except Exception as exc:
        report_exception(exc, context="surfaces.interactive_shell.turn")
        await emit(AgentEvent(type="turn_error", error=exc))
    finally:
        runtime.state.finish_dispatch(dispatch_cancel)
        await emit(AgentEvent(type="turn_end"))


async def run_input_loop(
    *,
    state: ReplState,
    session: Session,
    background: BackgroundTaskManager | None,
    input_reader: PromptInputReader,
    echo_console: Console,
    handle_input_action: Callable[[InputAction], Awaitable[bool]],
) -> None:
    """Run the interactive session's main input loop until exit or close.

    This loop reads input; it does not run agent turns itself. Each raw input
    event is classified into an ``InputAction`` by ``decide_input_action`` and
    handed to ``handle_input_action``. For a submitted prompt that handler pushes
    the text onto ``state.queue``; the queued text is then consumed
    asynchronously by ``run_agent_turn_queue`` (started in the controller's
    ``_start_runtime_services``), which runs each turn via ``run_agent_turn``.

    Keeping input reading and turn execution as two separate loops joined only by
    ``state.queue`` is deliberate: it lets the user keep typing, cancel, or
    answer a confirmation while a turn is still in flight.
    """
    while not state.exit_requested:
        if background is not None:
            background.drain_turn_start_output(echo_console)
        event = await input_reader.read()
        action = decide_input_action(
            event,
            ShellInputSnapshot(
                exit_requested=state.exit_requested,
                dispatch_running=state.is_dispatch_running(),
                awaiting_confirmation=state.is_awaiting_confirmation(),
            ),
            needs_exclusive_stdin=lambda text: turn_needs_exclusive_stdin(
                text,
                session,
            ),
        )
        should_continue = await handle_input_action(action)
        if not should_continue:
            return


async def run_agent_turn_queue(
    *,
    state: ReplState,
    run_turn: Callable[[str], Coroutine[Any, Any, None]],
) -> None:
    """Consume queued turns and run each one until exit."""
    while not state.exit_requested:
        try:
            text = await state.queue.get()
        except asyncio.CancelledError:
            return
        if state.exit_requested:
            state.queue.task_done()
            return

        turn_task = asyncio.create_task(run_turn(text))
        state.attach_turn_task(turn_task)
        try:
            await turn_task
        except asyncio.CancelledError:
            _logger.debug("Queued turn task was cancelled")
        except Exception as exc:
            _logger.debug("Queued turn task ended with exception: %s", exc)
        finally:
            state.clear_current_task()
            state.queue.task_done()


__all__ = [
    "AgentTurnRuntime",
    "run_agent_turn",
    "run_agent_turn_queue",
    "run_input_loop",
]
