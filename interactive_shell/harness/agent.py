"""Terminal assistant and turn handling for the interactive OpenSRE shell.

This module coordinates one shell turn:

1. run tool-calling actions,
2. answer or summarize when needed,
3. manage turn lifecycle and cancellation.

Prompt construction lives in ``harness/llm_context/cli_agent_prompt.py`` and
terminal presentation lives in ``runtime/agent_presentation.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from rich.console import Console
from rich.markup import escape

from config.llm_reasoning_effort import apply_reasoning_effort
from integrations.llm_cli.errors import CLITimeoutError
from interactive_shell.harness.llm_context.cli_agent_prompt import build_cli_agent_prompt
from interactive_shell.harness.llm_context.conversation_history import (
    MAX_CONVERSATION_MESSAGES,
)
from interactive_shell.harness.tool_calling import run_tool_calling_turn
from interactive_shell.harness.turn_context import TurnContext
from interactive_shell.runtime import ReplSession
from interactive_shell.runtime.agent_presentation import (
    AgentEvent,
    AgentEventSink,
    ConsoleAgentEventSink,
    render_json_like_response,
)
from interactive_shell.runtime.background.workers import BackgroundTaskManager
from interactive_shell.runtime.core.state import (
    PROMPT_REFRESH_INTERVAL_S,
    ReplState,
    SpinnerState,
)
from interactive_shell.runtime.core.token_accounting import build_llm_run_info
from interactive_shell.runtime.core.turn_accounting import (
    ShellTurnAccounting,
    ShellTurnResult,
    ToolCallingTurnResult,
)
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
)
from interactive_shell.tools.tool_gathering import gather_tool_evidence
from interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    STREAM_LABEL_ASSISTANT,
    WARNING,
    stream_to_console,
)
from interactive_shell.ui.output.repl_progress import repl_safe_progress_scope
from interactive_shell.ui.streaming.console import StreamingConsole
from interactive_shell.utils.error_handling.exception_reporting import report_exception
from interactive_shell.utils.telemetry import LlmRunInfo, PromptRecorder
from platform.analytics.repl_context import bind_cli_session_id, reset_cli_session_id

_logger = logging.getLogger(__name__)

_AGENT_TURN_KIND = "agent"

RunToolCallingTurn = Callable[..., ToolCallingTurnResult]
GatherEvidence = Callable[..., str | None]
AnswerAgent = Callable[..., LlmRunInfo | None]


# ---------------------------------------------------------------------------
# 1. Action parsing
# ---------------------------------------------------------------------------


_ALLOWED_SLASH_ACTIONS = frozenset(
    {
        "/model show",
        "/health",
        "/doctor",
        "/version",
    }
)

# Conversational action kinds map onto the same capability gates the action
# planner uses, so a session that explicitly disables a surface cannot actuate
# it from the chat answer path either.
_ACTION_CAPABILITY: dict[str, str] = {
    "switch_llm_provider": "llm_provider",
    "switch_toolcall_model": "llm_provider",
    "slash": "slash_commands",
    "run_interactive": "slash_commands",
    "run_cli_command": "cli_commands",
}


def _as_text(value: object) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class ActionPlanAction:
    """Typed representation of a single action emitted by the CLI agent."""

    kind: str
    provider: str = ""
    model: str = ""
    toolcall_model: str = ""
    command: str = ""
    args: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ActionPlanAction | None:
        kind = _as_text(payload.get("action"))

        if not kind and _as_text(payload.get("provider")):
            kind = "switch_llm_provider"

        if not kind and _as_text(payload.get("command")):
            kind = "slash"

        if not kind:
            return None

        return cls(
            kind=kind,
            provider=_as_text(payload.get("provider")),
            model=_as_text(payload.get("model")),
            toolcall_model=_as_text(payload.get("toolcall_model")),
            command=_as_text(payload.get("command")),
            args=_as_text(payload.get("args")),
        )

    @property
    def capability(self) -> str | None:
        return _ACTION_CAPABILITY.get(self.kind)

    @property
    def label(self) -> str:
        if self.kind == "switch_llm_provider":
            text = f"switch LLM provider to {self.provider}"
            if self.model:
                text += f" ({self.model})"
            if self.toolcall_model:
                text += f" + toolcall {self.toolcall_model}"
            return text

        if self.kind == "switch_toolcall_model":
            return (
                f"switch toolcall model to {self.model}" if self.model else "switch toolcall model"
            )

        if self.kind == "slash":
            return self.command

        if self.kind == "run_cli_command":
            return f"opensre {self.args}" if self.args else "opensre"

        if self.kind == "run_interactive":
            return self.command or "interactive command"

        return f"unsupported action: {self.kind or '?'}"


def _extract_json_object(text: str) -> dict[str, object] | None:
    """Find the first top-level JSON object embedded in *text* (pure)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1]).strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _parse_action_plan(text: str) -> tuple[ActionPlanAction, ...]:
    """Parse raw model output into an immutable action plan (pure)."""
    payload = _extract_json_object(text)
    if payload is None:
        return ()

    actions = payload.get("actions")
    if not isinstance(actions, list):
        single = ActionPlanAction.from_payload(payload)
        return (single,) if single is not None else ()

    return tuple(
        action
        for raw in actions
        if isinstance(raw, dict)
        for action in (ActionPlanAction.from_payload(raw),)
        if action is not None
    )


# ---------------------------------------------------------------------------
# 2. Action execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionPlanningEnv:
    """Immutable snapshot of everything action execution needs from the world."""

    allowed_slash_actions: frozenset[str]
    registered_slash_commands: frozenset[str]
    configured_integrations_known: bool
    configured_integrations_count: int
    disabled_capabilities: frozenset[str]
    repl_tty_interactive: bool


def _read_action_planning_env(session: ReplSession) -> ActionPlanningEnv:
    """Read the live world once into a frozen execution environment."""
    from interactive_shell.command_registry import SLASH_COMMANDS
    from interactive_shell.tools.tool_contracts import capability_not_explicitly_disabled
    from interactive_shell.ui.components.choice_menu import repl_tty_interactive

    disabled = frozenset(
        capability
        for capability in frozenset(_ACTION_CAPABILITY.values())
        if not capability_not_explicitly_disabled(session, capability)
    )
    return ActionPlanningEnv(
        allowed_slash_actions=_ALLOWED_SLASH_ACTIONS,
        registered_slash_commands=frozenset(SLASH_COMMANDS),
        configured_integrations_known=session.configured_integrations_known,
        configured_integrations_count=len(session.configured_integrations),
        disabled_capabilities=disabled,
        repl_tty_interactive=repl_tty_interactive(),
    )


def _filter_actions_by_capabilities(
    actions: tuple[ActionPlanAction, ...], env: ActionPlanningEnv
) -> tuple[ActionPlanAction, ...]:
    """Drop actions whose capability surface is explicitly disabled (pure)."""
    return tuple(
        action
        for action in actions
        if action.capability is None or action.capability not in env.disabled_capabilities
    )


# `run_interactive` is not a narrow feature allowlist. It is the bridge from an
# agent-planned action back into the OpenSRE interactive shell. Any command that
# is registered in the slash-command registry is already an OpenSRE command and
# must stay eligible here.
#
# Keep this registry-backed instead of listing subcommands like
# `/integrations setup` or `/integrations remove`: duplicating subcommand lists
# here drifts from the actual dispatcher and causes valid OpenSRE commands to be
# rejected before the normal policy/confirmation flow can evaluate them. The
# dispatcher remains the source of truth for argument validation, execution tier,
# confirmation, exclusive-stdin handling, and the command's side effects.
#
# The only thing this gate should reject is non-OpenSRE input: empty strings,
# shell snippets, arbitrary text, or unknown slash commands. Do not reintroduce
# a per-command allowlist in this file.
def _registered_interactive_command(command: str, registered: frozenset[str]) -> bool:
    """True when *command* names a registered OpenSRE slash command (pure)."""
    parts = command.strip().split()
    if not parts:
        return False
    name = parts[0].lower()
    if name == "/":
        return True
    if not name.startswith("/"):
        return False
    return name in registered


def _integration_command_blocked(payload: str, env: ActionPlanningEnv) -> bool:
    """Block integration-management CLI runs when none are configured (pure)."""
    if not env.configured_integrations_known or env.configured_integrations_count:
        return False
    lowered = payload.strip().lower()
    return lowered.startswith("integrations") or "integration" in lowered


@dataclass(frozen=True)
class ActionRuntime:
    """Boundary objects the action handlers need to perform their effects."""

    session: ReplSession
    console: Console
    confirm_fn: Callable[[str], str] | None
    is_tty: bool | None


def _print_error(console: Console, message: str) -> None:
    console.print(f"[{ERROR}]{escape(message)}[/]")


def _model_set_command(action: ActionPlanAction) -> str:
    command = f"/model set {action.provider}"
    if action.model:
        command += f" {action.model}"
    if action.toolcall_model:
        command += f" --toolcall-model {action.toolcall_model}"
    return command


def _execution_allowed(*, tool: str, summary: str, runtime: ActionRuntime) -> bool:
    """Resolve the execution policy / confirmation for one action (boundary)."""
    from interactive_shell.tools.shared import allow_tool
    from interactive_shell.ui.execution_confirm import execution_allowed

    return execution_allowed(
        allow_tool(tool),
        session=runtime.session,
        console=runtime.console,
        action_summary=summary,
        confirm_fn=runtime.confirm_fn,
        is_tty=runtime.is_tty,
        action_already_listed=True,
    )


def _render_requested_actions(console: Console, actions: tuple[ActionPlanAction, ...]) -> None:
    console.print()
    console.print(f"[{BOLD_BRAND}]{STREAM_LABEL_ASSISTANT}:[/]")
    console.print(f"[{DIM}]Requested actions:[/]")
    for index, action in enumerate(actions, start=1):
        console.print(f"[{DIM}]{index}.[/] [{BOLD_BRAND}]{escape(action.label)}[/]")
    console.print()


def _execute_switch_llm_provider(action: ActionPlanAction, runtime: ActionRuntime) -> None:
    if not action.provider:
        _print_error(runtime.console, "missing provider for switch_llm_provider action")
        return

    command = _model_set_command(action)
    if not _execution_allowed(tool="switch_llm_provider", summary=command, runtime=runtime):
        return

    from interactive_shell.command_registry import switch_llm_provider

    runtime.console.print(f"[bold]$ {escape(command)}[/bold]")
    switch_llm_provider(
        action.provider,
        runtime.console,
        model=action.model or None,
        toolcall_model=action.toolcall_model or None,
    )
    runtime.session.record("slash", command, ok=True)


def _execute_switch_toolcall_model(action: ActionPlanAction, runtime: ActionRuntime) -> None:
    if not action.model:
        _print_error(runtime.console, "missing model for switch_toolcall_model action")
        return

    command = f"/model toolcall set {action.model}"
    if not _execution_allowed(tool="switch_toolcall_model", summary=command, runtime=runtime):
        return

    from interactive_shell.command_registry import switch_toolcall_model

    runtime.console.print(f"[bold]$ {escape(command)}[/bold]")
    switch_toolcall_model(action.model, runtime.console)
    runtime.session.record("slash", command, ok=True)


def _execute_slash_action(
    action: ActionPlanAction, runtime: ActionRuntime, env: ActionPlanningEnv
) -> None:
    command = action.command
    if command not in env.allowed_slash_actions:
        _print_error(runtime.console, f"unsupported action command: {command}")
        return

    from interactive_shell.command_registry import dispatch_slash

    stripped = command.strip()
    name = stripped.split()[0].lower()

    # Unknown to the dispatcher: hand straight to dispatch_slash, which renders
    # its own "unknown command" feedback (no policy preclear).
    if name not in env.registered_slash_commands:
        dispatch_slash(
            command,
            runtime.session,
            runtime.console,
            confirm_fn=runtime.confirm_fn,
            is_tty=runtime.is_tty,
            policy_precleared=False,
        )
        return

    if not _execution_allowed(tool="slash", summary=stripped, runtime=runtime):
        runtime.session.record("slash", stripped, ok=False)
        return

    runtime.console.print(f"[bold]$ {escape(command)}[/bold]")
    dispatch_slash(
        command,
        runtime.session,
        runtime.console,
        confirm_fn=runtime.confirm_fn,
        is_tty=runtime.is_tty,
        policy_precleared=True,
    )


def _execute_cli_command(
    action: ActionPlanAction, runtime: ActionRuntime, env: ActionPlanningEnv
) -> None:
    if not action.args:
        _print_error(runtime.console, "missing args for run_cli_command action")
        return

    if _integration_command_blocked(action.args, env):
        runtime.console.print(
            f"[{WARNING}]integration command blocked: no integrations are configured "
            "in this session.[/]"
        )
        return

    from interactive_shell.runtime.subprocess_runner import run_opensre_cli_command

    run_opensre_cli_command(
        action.args,
        runtime.session,
        runtime.console,
        confirm_fn=runtime.confirm_fn,
        is_tty=runtime.is_tty,
    )


def _execute_interactive_command(
    action: ActionPlanAction, runtime: ActionRuntime, env: ActionPlanningEnv
) -> None:
    command = action.command
    if not _registered_interactive_command(command, env.registered_slash_commands):
        _print_error(runtime.console, f"unsupported interactive command: {command}")
        return

    if not env.repl_tty_interactive:
        runtime.console.print(
            f"Run [bold]{escape(command)}[/bold] in the interactive shell to continue."
        )
        return

    runtime.console.print(f"[{DIM}]Launching[/] [{BOLD_BRAND}]{escape(command)}[/]…")
    runtime.session.queue_auto_command(command)


def _execute_action(
    action: ActionPlanAction, runtime: ActionRuntime, env: ActionPlanningEnv
) -> None:
    match action.kind:
        case "switch_llm_provider":
            _execute_switch_llm_provider(action, runtime)
        case "switch_toolcall_model":
            _execute_switch_toolcall_model(action, runtime)
        case "slash":
            _execute_slash_action(action, runtime, env)
        case "run_cli_command":
            _execute_cli_command(action, runtime, env)
        case "run_interactive":
            _execute_interactive_command(action, runtime, env)
        case _:
            _print_error(runtime.console, f"unsupported action: {action.kind or '?'}")


def _execute_action_plan(
    actions: tuple[ActionPlanAction, ...],
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    """Execute an action plan directly; return True iff anything was eligible."""
    if not actions:
        return False

    env = _read_action_planning_env(session)
    allowed = _filter_actions_by_capabilities(tuple(actions), env)
    if not allowed:
        return False

    runtime = ActionRuntime(
        session=session,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )

    _render_requested_actions(console, allowed)
    for action in allowed:
        console.print()
        _execute_action(action, runtime, env)
    console.print()
    return True


# ---------------------------------------------------------------------------
# 3. Assistant answering
# ---------------------------------------------------------------------------


def _load_reasoning_client(console: Console) -> Any | None:
    try:
        from core.runtime.llm.llm_client import get_llm_for_reasoning
    except Exception as exc:
        report_exception(exc, context="interactive_shell.cli_agent.import")
        console.print(f"[{ERROR}]LLM client unavailable:[/] {escape(str(exc))}")
        return None

    return get_llm_for_reasoning()


def _stream_cli_agent_response(
    *,
    client: Any,
    prompt: str,
    session: ReplSession,
    console: Console,
) -> LlmRunInfo | None:
    try:
        started = time.monotonic()
        text_str = stream_to_console(
            console,
            label=STREAM_LABEL_ASSISTANT,
            chunks=client.invoke_stream(prompt),
            suppress_if_starts_with="{",
        )
    except KeyboardInterrupt:
        console.print(f"[{DIM}]· cancelled[/]")
        return None
    except Exception as exc:
        report_exception(
            exc,
            context="interactive_shell.cli_agent.stream",
            expected=isinstance(exc, CLITimeoutError),
        )
        console.print(f"[{ERROR}]assistant failed:[/] {escape(str(exc))}")
        return None

    return build_llm_run_info(
        session=session,
        prompt=prompt,
        response_text=text_str,
        started=started,
        client=client,
    )


def _record_cli_agent_turn(session: ReplSession, message: str, assistant_text: str) -> None:
    session.cli_agent_messages.append(("user", message))
    session.cli_agent_messages.append(("assistant", assistant_text))
    if len(session.cli_agent_messages) > MAX_CONVERSATION_MESSAGES:
        session.cli_agent_messages[:] = session.cli_agent_messages[-MAX_CONVERSATION_MESSAGES:]


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

    ``turn_ctx`` is the immutable per-turn snapshot assembled at turn start.
    When present, snapshot fields (conversation history, integration state,
    prior investigation, synthetic-run path) are read from it rather than from
    the live session, so prompt construction reflects a stable turn-start view.
    """
    client = _load_reasoning_client(console)
    if client is None:
        return None

    ctx = turn_ctx or TurnContext.from_session(message, session)

    prompt = build_cli_agent_prompt(
        message=message,
        session=session,
        tool_observation=tool_observation,
        tool_observation_on_screen=tool_observation_on_screen,
        turn_ctx=ctx,
    )

    run_info = _stream_cli_agent_response(
        client=client,
        prompt=prompt,
        session=session,
        console=console,
    )
    if run_info is None:
        return None

    text_str = run_info.response_text or ""
    handled = _execute_action_plan(
        _parse_action_plan(text_str),
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )

    _record_cli_agent_turn(session, message, text_str)

    if not handled:
        render_json_like_response(console, text_str)

    return run_info


def _response_text(run: LlmRunInfo | None) -> str:
    return run.response_text if run is not None and run.response_text else ""


# ---------------------------------------------------------------------------
# 4. Turn routing
# ---------------------------------------------------------------------------


def _route_turn(
    action_result: ToolCallingTurnResult, observation: str | None
) -> Literal["summarize_observation", "handled_without_llm", "gather_and_answer"]:
    """Decide the turn path from the action result and any left-over observation."""
    if (
        action_result.handled
        and observation is not None
        and action_result.executed_success_count > 0
    ):
        return "summarize_observation"
    if action_result.handled:
        return "handled_without_llm"
    return "gather_and_answer"


def _gather_and_answer(
    *,
    text: str,
    session: ReplSession,
    console: Console,
    gather_evidence: GatherEvidence,
    answer_agent: AnswerAgent,
    confirm_fn: Callable[[str], str] | None,
    is_tty: bool | None,
    turn_ctx: TurnContext,
) -> LlmRunInfo | None:
    gathered = gather_evidence(text, session, console, is_tty=is_tty)

    # When evidence was gathered, mark it off-screen so the prompt builder
    # includes it. When nothing was gathered, omit the flag entirely so the
    # call shape matches the plain conversational (no-observation) path.
    on_screen: dict[str, bool] = {"tool_observation_on_screen": False} if gathered else {}

    return answer_agent(
        text,
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        tool_observation=gathered or None,
        turn_ctx=turn_ctx,
        **on_screen,
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
    """Run one interactive-shell turn through three paths, in order:

    1. ``summarize_observation`` — a successful action left discovery output, so
       summarize it into a direct answer.
    2. ``handled_without_llm`` — the action fully handled the turn; stop without the LLM.
    3. ``gather_and_answer`` — nothing was handled; gather evidence and answer.

    The path choice is the pure ``_route_turn``; this function is the imperative
    shell that performs the chosen path's effects.
    """
    execute_actions = execute_actions or run_tool_calling_turn
    gather_evidence = gather_evidence or gather_tool_evidence
    answer_agent = answer_agent or answer_cli_agent

    # Snapshot session state before any turn mutations. Both the action agent
    # and the conversational assistant read from this frozen context so their
    # prompts reflect a consistent turn-start view rather than live session state.
    turn_ctx = TurnContext.from_session(text, session)
    accounting = ShellTurnAccounting(session=session, text=text, recorder=recorder)

    # Clear any observation left by a prior turn so only this turn's discovery
    # output can trigger a summary pass.
    session.last_command_observation = None

    action_result = execute_actions(
        text,
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        turn_ctx=turn_ctx,
    )
    accounting.record_action_result(action_result)

    observation = session.last_command_observation

    route = _route_turn(action_result, observation)
    if route == "summarize_observation":
        with apply_reasoning_effort(turn_ctx.reasoning_effort):
            run = answer_agent(
                text,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                tool_observation=observation,
                turn_ctx=turn_ctx,
            )
        result = ShellTurnResult(
            final_intent="cli_agent_summarized",
            action_result=action_result,
            assistant_response_text=_response_text(run),
            llm_run=run,
        )
    elif route == "handled_without_llm":
        result = ShellTurnResult(
            final_intent="cli_agent_handled",
            action_result=action_result,
            assistant_response_text=action_result.response_text,
        )
    elif route == "gather_and_answer":
        with apply_reasoning_effort(turn_ctx.reasoning_effort):
            run = _gather_and_answer(
                text=text,
                session=session,
                console=console,
                gather_evidence=gather_evidence,
                answer_agent=answer_agent,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                turn_ctx=turn_ctx,
            )
        result = ShellTurnResult(
            final_intent="cli_agent_fallback",
            action_result=action_result,
            assistant_response_text=_response_text(run),
            llm_run=run,
        )
    else:
        raise AssertionError(f"unreachable turn route: {route!r}")

    return accounting.finalize(result)


# ---------------------------------------------------------------------------
# 5. Runtime loop
# ---------------------------------------------------------------------------


class DispatchCancelled(Exception):
    """Raised when in-flight dispatch is cancelled during confirmation."""


@contextlib.contextmanager
def _bound_cli_session(session_id: str) -> Iterator[None]:
    token = bind_cli_session_id(session_id)
    try:
        yield
    finally:
        reset_cli_session_id(token)


class AgentTurnRunner:
    """Drives one submitted shell turn: presentation setup and lifecycle."""

    def __init__(
        self,
        *,
        session: ReplSession,
        state: ReplState,
        spinner: SpinnerState,
        invalidate_prompt: Callable[[], None],
    ) -> None:
        self.session = session
        self.state = state
        self.spinner = spinner
        self.invalidate_prompt = invalidate_prompt

    async def run_agent_turn(self, text: str) -> None:
        """Set up shell presentation for one turn and drive its lifecycle."""
        dispatch_cancel = threading.Event()
        console = StreamingConsole(
            self.spinner,
            dispatch_cancel,
            prompt_invalidator=self.invalidate_prompt,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        emit = ConsoleAgentEventSink(
            session=self.session,
            spinner=self.spinner,
            console=console,
        )
        recorder = PromptRecorder.start(
            session=self.session,
            text=text,
            turn_kind=_AGENT_TURN_KIND,
        )
        progress_scope = (
            contextlib.nullcontext()
            if turn_needs_exclusive_stdin(text, self.session)
            else repl_safe_progress_scope()
        )
        with progress_scope:
            await _run_agent_turn_loop(
                session=self.session,
                state=self.state,
                text=text,
                output=console,
                recorder=recorder,
                confirm=lambda prompt: request_confirmation_via_prompt(self.state, prompt),
                emit=emit,
                dispatch_cancel=dispatch_cancel,
            )


async def _run_agent_turn_loop(
    *,
    session: ReplSession,
    state: ReplState,
    text: str,
    output: StreamingConsole,
    recorder: PromptRecorder | None,
    confirm: Callable[[str], str],
    emit: AgentEventSink,
    dispatch_cancel: threading.Event,
) -> None:
    current_task = asyncio.current_task()
    if current_task is not None:
        state.start_dispatch(task=current_task, cancel_event=dispatch_cancel)
    else:
        state.attach_cancel_event(dispatch_cancel)

    await emit(AgentEvent(type="turn_start", text=text))
    try:
        await _execute_agent_turn(
            session=session,
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
        state.finish_dispatch(dispatch_cancel)
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
