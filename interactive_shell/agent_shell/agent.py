"""Terminal lifecycle + turn entry for the interactive OpenSRE shell.

The agentic turn engine itself now lives in the decoupled :mod:`agent` package
(``core.agent.engine`` for routing + the conversational assistant, ``core.agent.driver``
for the action tool-calling turn, ``core.agent.gather`` for evidence gathering). This
module keeps only what is intrinsically terminal:

* ``handle_message_with_agent`` / ``answer_cli_agent`` — thin shell entry points
  that build the :mod:`core.agent.ports` adapters and delegate to the engine.
* the async REPL plumbing (``AgentTurnRunner``, ``ConsoleAgentEventSink``, the
  input/queue loops, prompt-driven confirmation) that drives presentation around
  each submitted turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from dataclasses import dataclass
from typing import Any, Literal

from rich.console import Console
from rich.markup import escape

from core.agent import engine
from core.agent.action_plan import ActionPlanAction, parse_action_plan
from core.agent.context import TurnContext
from core.agent.results import ShellTurnResult, ToolCallingTurnResult
from interactive_shell.agent_shell.adapters import (
    ShellActionDispatch,
    ShellErrorReporter,
    ShellOutputSink,
    ShellPromptContextProvider,
    ShellReasoningClientProvider,
    ShellRunRecordFactory,
)
from interactive_shell.agent_shell.tool_calling import run_tool_calling_turn
from interactive_shell.runtime import ReplSession
from interactive_shell.runtime.background.workers import BackgroundTaskManager
from interactive_shell.runtime.core.state import (
    PROMPT_REFRESH_INTERVAL_S,
    ReplState,
    SpinnerState,
)
from interactive_shell.runtime.core.turn_accounting import ShellTurnAccounting
from interactive_shell.runtime.input import (
    PromptInputReader,
)
from interactive_shell.runtime.input.actions import (
    InputAction,
    ShellInputSnapshot,
    decide_input_action,
)
from interactive_shell.runtime.utils.input_policy import (
    turn_needs_exclusive_stdin,
    turn_should_show_spinner,
)
from interactive_shell.tools.tool_gathering import gather_tool_evidence
from interactive_shell.ui import (
    ERROR,
    WARNING,
)
from interactive_shell.ui.components.cpr_stdin import drain_stale_cpr_bytes
from interactive_shell.ui.output.repl_progress import repl_safe_progress_scope
from interactive_shell.ui.streaming.console import StreamingConsole
from interactive_shell.utils.error_handling.exception_reporting import report_exception
from interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder
from platform.analytics.repl_context import bind_cli_session_id, reset_cli_session_id

# Re-exported for the parser's pure helpers; execution lives in action_dispatch.
_parse_action_plan = parse_action_plan

_logger = logging.getLogger(__name__)

_AGENT_TURN_KIND = "agent"

# Dependency seams used by the harness turn-routing tests.
RunToolCallingTurn = Callable[..., ToolCallingTurnResult]
GatherEvidence = Callable[..., "str | None"]
AnswerAgent = Callable[..., "LlmRunInfo | None"]


# ---------------------------------------------------------------------------
# Conversational assistant + turn routing (shell entry -> core.agent.engine)
# ---------------------------------------------------------------------------


def answer_cli_agent(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    tool_observation: str | None = None,
    tool_observation_on_screen: bool = True,
    turn_ctx: TurnContext | None = None,
) -> LlmRunInfo | None:
    """Run one turn of the terminal assistant (guidance only; no investigation run).

    Delegates to :func:`core.agent.engine.answer_cli_agent`, supplying the shell
    adapters (Rich output, grounding caches, reasoning client, telemetry, action
    dispatch).
    """
    return engine.answer_cli_agent(
        message,
        session,
        ShellOutputSink(console),
        prompts=ShellPromptContextProvider(session),
        reasoning=ShellReasoningClientProvider(console),
        run_factory=ShellRunRecordFactory(session),
        dispatch=ShellActionDispatch(session, console),
        error_reporter=ShellErrorReporter(),
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        tool_observation=tool_observation,
        tool_observation_on_screen=tool_observation_on_screen,
        turn_ctx=turn_ctx,
    )


def handle_message_with_agent(
    text: str,
    session: ReplSession,
    console: Console,
    *,
    recorder: PromptRecorder | None,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    execute_actions: RunToolCallingTurn | None = None,
    gather_evidence: GatherEvidence | None = None,
    answer_agent: AnswerAgent | None = None,
) -> ShellTurnResult:
    """Run one interactive-shell turn through the decoupled three-path engine.

    The action driver, gather pass, and conversational assistant are bound to the
    live ``session``/``console`` here (so injected test doubles keep their
    ``(text, session, console, ...)`` shape) and handed to
    :func:`core.agent.engine.run_turn`, which performs the pure path routing.
    """
    _execute = execute_actions or run_tool_calling_turn
    _gather = gather_evidence or gather_tool_evidence
    _answer = answer_agent or answer_cli_agent
    accounting = ShellTurnAccounting(session=session, text=text, recorder=recorder)

    def execute_bound(
        t: str,
        *,
        confirm_fn: Callable[[str], str] | None = None,
        is_tty: bool | None = None,
        turn_ctx: TurnContext | None = None,
    ) -> ToolCallingTurnResult:
        return _execute(
            t, session, console, confirm_fn=confirm_fn, is_tty=is_tty, turn_ctx=turn_ctx
        )

    def answer_bound(t: str, **kwargs: Any) -> LlmRunInfo | None:
        # Pure passthrough so the engine controls the exact call shape: when it
        # omits ``tool_observation_on_screen`` (no evidence gathered) the bound
        # call omits it too, matching the plain conversational path.
        return _answer(t, session, console, **kwargs)

    def gather_bound(t: str, *, is_tty: bool | None = None) -> str | None:
        return _gather(t, session, console, is_tty=is_tty)

    return engine.run_turn(
        text,
        session,
        execute_actions=execute_bound,
        answer=answer_bound,
        gather=gather_bound,
        accounting=accounting,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )


# ---------------------------------------------------------------------------
# Agent lifecycle: pure presentation reducer + effectful transition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentEvent:
    """Agent lifecycle event emitted during one submitted shell turn."""

    type: Literal["turn_start", "turn_interrupted", "turn_error", "turn_end"]
    text: str | None = None
    error: Exception | None = None


AgentEventSink = Callable[[AgentEvent], Awaitable[None]]


class DispatchCancelled(Exception):
    """Raised when in-flight dispatch is cancelled during confirmation."""


@contextlib.contextmanager
def _bound_cli_session(session_id: str) -> Iterator[None]:
    token = bind_cli_session_id(session_id)
    try:
        yield
    finally:
        reset_cli_session_id(token)


@dataclass(frozen=True)
class AgentPresentationState:
    """Immutable presentation state evolved across lifecycle events."""

    show_spinner: bool = False
    prompt_suppressed: bool = False


def _reduce_agent_presentation(
    state: AgentPresentationState,
    event: AgentEvent,
    *,
    should_show_spinner: bool,
) -> AgentPresentationState:
    """Compute the next presentation state for *event* (pure)."""
    match event.type:
        case "turn_start":
            return AgentPresentationState(
                show_spinner=should_show_spinner,
                prompt_suppressed=should_show_spinner,
            )
        case "turn_end":
            return AgentPresentationState()
        case "turn_interrupted" | "turn_error":
            return state
        case _:
            raise ValueError(f"Unknown agent event type: {event.type!r}")


async def _render_agent_presentation_transition(
    *,
    previous: AgentPresentationState,
    current: AgentPresentationState,
    event: AgentEvent,
    console: StreamingConsole,
    spinner: SpinnerState,
) -> None:
    """Perform the terminal side effects for one presentation transition."""
    from interactive_shell.ui.output import set_prompt_suppress_fn

    match event.type:
        case "turn_start":
            if current.show_spinner:
                spinner.start()
                set_prompt_suppress_fn(console.suppress_prompt_spinner)
        case "turn_interrupted":
            console.print(f"[{WARNING}]· interrupted[/]")
        case "turn_error":
            exc = event.error
            if exc is None:
                raise ValueError("turn_error event requires an error")
            console.print(f"[{ERROR}]turn error:[/] {escape(str(exc))}")
        case "turn_end":
            set_prompt_suppress_fn(None)
            if previous.show_spinner:
                spinner.stop()
            await asyncio.sleep(0.05)
            drain_stale_cpr_bytes()
        case _:
            raise ValueError(f"Unknown agent event type: {event.type!r}")


class ConsoleAgentEventSink:
    """Render agent lifecycle events to the terminal console.

    Imperative shell: it holds the evolving ``AgentPresentationState`` and routes
    each event through the pure ``_reduce_agent_presentation`` reducer and the
    effectful ``_render_agent_presentation_transition`` renderer.
    """

    def __init__(
        self,
        *,
        session: ReplSession,
        spinner: SpinnerState,
        console: StreamingConsole,
    ) -> None:
        self.session = session
        self.spinner = spinner
        self.console = console
        self.state = AgentPresentationState()

    async def __call__(self, event: AgentEvent) -> None:
        previous = self.state
        self.state = _reduce_agent_presentation(
            previous,
            event,
            should_show_spinner=turn_should_show_spinner(event.text or "", self.session),
        )
        await _render_agent_presentation_transition(
            previous=previous,
            current=self.state,
            event=event,
            console=self.console,
            spinner=self.spinner,
        )


# ---------------------------------------------------------------------------
# Per-turn runtime: functional record + driver, with class compat wrapper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentTurnRuntime:
    """Immutable dependencies for running one submitted shell turn."""

    session: ReplSession
    state: ReplState
    spinner: SpinnerState
    invalidate_prompt: Callable[[], None]


async def run_agent_turn(runtime: AgentTurnRuntime, text: str) -> None:
    """Set up shell presentation for one turn and drive its lifecycle."""
    dispatch_cancel = threading.Event()
    console = StreamingConsole(
        runtime.spinner,
        dispatch_cancel,
        prompt_invalidator=runtime.invalidate_prompt,
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
    progress_scope = (
        contextlib.nullcontext()
        if turn_needs_exclusive_stdin(text, runtime.session)
        else repl_safe_progress_scope()
    )
    with progress_scope:
        await _run_agent_turn_loop(
            runtime=runtime,
            text=text,
            output=console,
            recorder=recorder,
            confirm=lambda prompt: request_confirmation_via_prompt(runtime.state, prompt),
            emit=emit,
            dispatch_cancel=dispatch_cancel,
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
        await _execute_agent_turn(
            session=runtime.session,
            text=text,
            output=output,
            recorder=recorder,
            confirm=confirm,
        )
    except asyncio.CancelledError:
        await emit(AgentEvent(type="turn_interrupted"))
        raise
    except DispatchCancelled:
        await emit(AgentEvent(type="turn_interrupted"))
    except Exception as exc:
        report_exception(exc, context="interactive_shell.turn")
        await emit(AgentEvent(type="turn_error", error=exc))
    finally:
        runtime.state.finish_dispatch(dispatch_cancel)
        await emit(AgentEvent(type="turn_end"))


async def _execute_agent_turn(
    *,
    session: ReplSession,
    text: str,
    output: StreamingConsole,
    recorder: PromptRecorder | None,
    confirm: Callable[[str], str],
) -> None:
    with _bound_cli_session(session.session_id):
        await asyncio.to_thread(
            handle_message_with_agent,
            text,
            session,
            output,
            recorder=recorder,
            confirm_fn=confirm,
            is_tty=None,
        )


class AgentTurnRunner:
    """Stable class API over the functional ``run_agent_turn`` driver."""

    def __init__(
        self,
        *,
        session: ReplSession,
        state: ReplState,
        spinner: SpinnerState,
        invalidate_prompt: Callable[[], None],
    ) -> None:
        self.runtime = AgentTurnRuntime(
            session=session,
            state=state,
            spinner=spinner,
            invalidate_prompt=invalidate_prompt,
        )

    @property
    def session(self) -> ReplSession:
        return self.runtime.session

    @property
    def state(self) -> ReplState:
        return self.runtime.state

    @property
    def spinner(self) -> SpinnerState:
        return self.runtime.spinner

    async def run_agent_turn(self, text: str) -> None:
        await run_agent_turn(self.runtime, text)


async def run_input_loop(
    *,
    state: ReplState,
    session: ReplSession,
    background: BackgroundTaskManager | None,
    input_reader: PromptInputReader,
    echo_console: Console,
    handle_input_action: Callable[[InputAction], Awaitable[bool]],
) -> None:
    """Read input events and dispatch them until exit or close is requested."""
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
    "ActionPlanAction",
    "AgentEvent",
    "AgentEventSink",
    "AgentTurnRunner",
    "DispatchCancelled",
    "answer_cli_agent",
    "handle_message_with_agent",
    "request_confirmation_via_prompt",
    "run_agent_turn_queue",
    "run_input_loop",
]
