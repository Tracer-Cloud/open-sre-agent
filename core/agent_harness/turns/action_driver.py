"""Action tool-calling turn driver (decoupled from any terminal surface).

Runs one turn through the shared :class:`core.agent.Agent` tool-calling
loop: it assembles the available agent tools (via a :class:`~core.agent_harness.ports.ToolProvider`),
drives the loop while a tool-event observer streams each tool call to the
surface, and summarizes the executed tool calls into a facts-only
:class:`~core.agent_harness.turns.turn_results.ToolCallingTurnResult`.

Accounting/analytics for the turn are the caller's concern (see
:class:`core.agent_harness.ports.TurnAccounting`); this module emits none itself.
"""

from __future__ import annotations

import json
import logging
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.agent import Agent
from core.agent.goals import Goal
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.llm_resolution import default_llm_factory
from core.agent_harness.ports import (
    ConfirmFn,
    ErrorReporter,
    OutputSink,
    SessionStore,
    ToolProvider,
)
from core.agent_harness.prompts import (
    build_action_system_prompt_envelope,
    build_action_user_message,
)
from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations
from core.agent_harness.turns.conversation_recording import record_conversation_turn
from core.agent_harness.turns.goal_review import build_goal_reviewer, tap_executed_tool_names
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.agent_harness.turns.wal_recorder import with_wal_recording
from core.events import runtime_event_callback_from_observer
from core.execution import ToolExecutionHooks, public_tool_input
from core.llm.failure_classification import is_context_length_overflow
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool_framework.tags import SUMMARIZE_OBSERVATION_TAG
from platform.analytics.react_turn import run_react_agent_with_telemetry
from platform.observability.trace.prompts import persist_turn_system_prompt
from platform.observability.trace.spans import component_span

log = logging.getLogger(__name__)

# Some hosted tool-calling models emit one tool call per assistant turn even when
# parallel tool calls are enabled. Keep the tool-calling loop bounded, but leave
# enough headroom for a *data-dependent* compound request that must run
# sequentially: each step waits for the previous tool's result before the next
# call can be emitted (e.g. "look up the weather and then send it to Slack" =
# Architecture audit needs headroom for clone + ≤3 agent-scan probes +
# 4 heuristic shells + cleanup + save observations (then a no-tool report).
_MAX_TOOL_CALLING_ITERATIONS = 13
_EXECUTED_HISTORY_TYPES = {
    "slash",
    "shell",
    "alert",
    "synthetic_test",
    "implementation",
    "cli_command",
}
# Action tools that append their own ``session.history`` row when executed.
# Keep this as the single catalogue: the shell observer and generic tool-result
# accounting both key off it so new tools cannot silently double-record turns.
SELF_RECORDING_ACTION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "alert_sample",
        "cli_exec",
        "code_implement",
        "investigation_start",
        "llm_set_provider",
        "shell_run",
        "slash_invoke",
        "synthetic_run",
        "task_cancel",
    }
)
INVESTIGATION_DISPATCH_TOOL_NAMES: frozenset[str] = frozenset(
    {"investigation_start", "alert_sample"}
)


@dataclass(frozen=True)
class ActionTurnPlan:
    agent: Agent[Any]
    user_message: str
    llm: Any
    max_iterations: int


@dataclass(frozen=True)
class ToolCallingDeps:
    """Optional dependency seams used by tests/harnesses."""

    llm_factory: Callable[[], Any] | None = None


class _StaticToolCallLLM:
    """Deterministic one-shot LLM used for explicit non-LLM shell commands."""

    def __init__(self, tool_calls: list[ToolCall]) -> None:
        self._tool_calls = tool_calls
        self._used = False

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = system
        _ = tools
        if self._used:
            return AgentLLMResponse(content="", tool_calls=[], raw_content=None)
        self._used = True
        return AgentLLMResponse(content="", tool_calls=self._tool_calls, raw_content=None)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.input} for tc in tool_calls
            ],
        }

    @staticmethod
    def build_tool_result_message(
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "content": json.dumps(
                [
                    {"id": tc.id, "name": tc.name, "result": result}
                    for tc, result in zip(tool_calls, results)
                ],
                default=str,
            ),
        }


def _response_text_from_history_entries(entries: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in entries:
        response_text = item.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            chunks.append(response_text.strip())
            continue
        chunks.append(_history_entry_fallback(item))
    return "\n".join(chunks)


def _history_entry_fallback(item: dict[str, Any]) -> str:
    kind = str(item.get("type", "action"))
    text = str(item.get("text", "")).strip()
    ok = bool(item.get("ok", True))
    status = "succeeded" if ok else "failed"
    if text:
        return f"{kind} {text} ({status})"
    return f"{kind} ({status})"


def _pop_turn_outcome_hint(session: SessionStore) -> str:
    # Outcome hint lives on the shell terminal facet; other sessions have none.
    terminal = getattr(session, "terminal", None)
    pop_hint = getattr(terminal, "pop_turn_outcome_hint", None)
    if not callable(pop_hint):
        return ""
    hint = pop_hint()
    return hint.strip() if isinstance(hint, str) else ""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return json.dumps(content, default=str)
    return str(content)


def _generic_tool_results(result: Any) -> list[tuple[ToolCall, Any]]:
    return [
        (tool_call, tool_result)
        for tool_call, tool_result in getattr(result, "tool_results", [])
        if tool_call.name not in SELF_RECORDING_ACTION_TOOL_NAMES
        and tool_call.name != "assistant_handoff"
    ]


def _format_generic_tool_payload(tool_call: ToolCall, tool_result: Any) -> str:
    """Build a user-visible summary for one non-self-recording tool result."""
    preferred_response = _preferred_tool_response_text(tool_result)
    if preferred_response:
        return preferred_response
    details = getattr(tool_result, "details", None)
    if isinstance(details, dict):
        summary = details.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        stdout = details.get("stdout")
        if details.get("ok") and isinstance(stdout, str) and stdout.strip():
            return stdout.strip()
        error = details.get("error")
        if error:
            return str(error).strip()
    if getattr(tool_result, "is_error", False):
        return ""
    content = _content_to_text(getattr(tool_result, "content", "")).strip()
    if not content:
        return ""
    # Prefer a nested summary when the tool returned a JSON object payload.
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        response_text = parsed.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            return response_text.strip()
        summary = parsed.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        if parsed.get("ok") and isinstance(parsed.get("stdout"), str) and parsed["stdout"].strip():
            return str(parsed["stdout"]).strip()
        if parsed.get("error"):
            return str(parsed["error"]).strip()
    args = public_tool_input(tool_call.input)
    if args:
        return (
            f"{tool_call.name} input: {json.dumps(args, ensure_ascii=False, default=str)}"
            f"\n{tool_call.name} result: {content}"
        )
    return f"{tool_call.name} result: {content}"


def _preferred_tool_response_text(tool_result: Any) -> str:
    details = getattr(tool_result, "details", None)
    if isinstance(details, dict):
        response_text = details.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            return response_text.strip()
    content = _content_to_text(getattr(tool_result, "content", "")).strip()
    if not content:
        return ""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    response_text = parsed.get("response_text")
    return response_text.strip() if isinstance(response_text, str) else ""


def _has_preferred_tool_response_text(result: Any) -> bool:
    return any(
        bool(_preferred_tool_response_text(tool_result))
        for _tool_call, tool_result in _generic_tool_results(result)
    )


def _self_recording_tools_only(result: Any) -> bool:
    """True when every executed tool (except handoff) already printed to the console.

    Those tools return a bare success flag to the model; any closing prose is
    invented without the command's on-screen output (e.g. claiming ``/health``
    was all-green after the report already showed failures).
    """
    names = [
        tool_call.name
        for tool_call, _tool_result in getattr(result, "tool_results", [])
        if tool_call.name != "assistant_handoff"
    ]
    return bool(names) and all(name in SELF_RECORDING_ACTION_TOOL_NAMES for name in names)


# Self-recording tools whose result payload carries the real command output
# back to the model (shell: stdout/stderr/exit_code; slash: the captured
# console output read back from the history row). A closing summary after a
# chain of these is grounded in observed output, unlike the bare success flags
# most self-recording tools return.
_GROUNDED_CHAIN_TOOL_NAMES: frozenset[str] = frozenset({"shell_run", "slash_invoke"})


def _multi_step_grounded_chain(result: Any) -> bool:
    """True when the turn chained two or more output-carrying tool steps.

    ``_self_recording_tools_only`` suppresses model closings because most
    self-recording tools hand the model a bare success flag, so closing prose
    would be invented. ``shell_run`` and ``slash_invoke`` are the exceptions —
    their tool results carry the real output back to the model — so after a
    multi-step chain the completion summary is grounded in output the model
    actually observed, and dropping it left workflow turns ending on raw step
    output with no wrap-up. Single commands keep the suppression: their one
    output block is already on screen, and a paraphrase only adds
    contradiction risk.
    """
    names = [
        tool_call.name
        for tool_call, _tool_result in getattr(result, "tool_results", [])
        if tool_call.name != "assistant_handoff"
    ]
    return len(names) >= 2 and all(name in _GROUNDED_CHAIN_TOOL_NAMES for name in names)


def _asks_the_user(final_text: str) -> bool:
    """True when the closing message asks the user something.

    A question ("Found 5 loops — remove all of them?") is direction-seeking,
    not a restatement of tool output, so the invented-summary hazard that
    justifies suppressing self-recording closings does not apply. Dropping it
    is worse than any paraphrase risk: the user sees dead air, and their "yes"
    has no recorded offer to resolve against on the next turn.
    """
    return final_text.rstrip().endswith("?")


def _response_text_from_generic_results(result: Any) -> str:
    chunks: list[str] = []
    for tool_call, tool_result in _generic_tool_results(result):
        formatted = _format_generic_tool_payload(tool_call, tool_result)
        if formatted:
            chunks.append(formatted)
    return "\n".join(chunks)


def _is_user_facing_final_text(text: str) -> bool:
    """True when post-tool model text should replace tool dumps and be streamed."""
    stripped = text.strip()
    if not stripped:
        return False
    if "\n" in stripped or stripped.startswith("#"):
        return True
    return len(stripped) > 60


def _generic_tool_result_counts(result: Any) -> tuple[int, int]:
    generic_results = _generic_tool_results(result)
    executed_count = len(generic_results)
    success_count = sum(
        1
        for _tool_call, tool_result in generic_results
        if not getattr(tool_result, "is_error", False)
    )
    return executed_count, success_count


def _should_stash_observation(
    result: Any,
    *,
    tools_by_name: dict[str, Any],
) -> bool:
    """True when a successful tool opted into observation summary via its tags."""
    for tool_call, tool_result in _generic_tool_results(result):
        if getattr(tool_result, "is_error", False):
            continue
        tool = tools_by_name.get(tool_call.name)
        tags = getattr(tool, "tags", ()) if tool is not None else ()
        if SUMMARIZE_OBSERVATION_TAG in tags:
            return True
    return False


def _turn_resolved_integrations(
    session: SessionStore,
    turn_plan: TurnPlan | None,
) -> dict[str, Any]:
    """The turn's single resolved-integration view: from the plan, else resolve once.

    ``build_turn_plan`` already resolved integrations, so the plan is trusted even
    when the result is empty (``{}`` means "no integrations", not "unresolved").
    Only the direct-call path with no plan (some tests, headless without a turn)
    resolves here.
    """
    if turn_plan is not None:
        return dict(turn_plan.resolved_integrations)
    return dict(resolve_and_cache_integrations(session))


def _persist_tool_calling_error(session: SessionStore, user_text: str, error_text: str) -> None:
    record_conversation_turn(session, user_text, error_text)


def _render_tool_calling_error(output: OutputSink, message: str) -> None:
    output.print()
    output.render_response_header("assistant")
    output.render_error(message)


def _stage_action_llm_failure(
    message: str,
    session: SessionStore,
    *,
    client: Any | None,
    error_text: str,
) -> None:
    """Stage telemetry for an action-agent LLM failure on conversational input.

    Explicit ``!shell`` / literal ``/slash`` turns never invoke the hosted LLM
    (they run through ``_StaticToolCallLLM``), so a failure there stays a
    terminal-action outcome. For conversational input the LLM was the intended
    route, so the turn must be reported as a failed LLM call — not a terminal
    turn tagged ``no_conversational_agent``.
    """
    if _bang_shell_command(message) is not None or message.strip().startswith("/"):
        return
    from core.agent_harness.turns.orchestrator import stage_turn_error, stage_turn_llm_failure

    stage_turn_error(session, "action_agent_error", error_text)
    stage_turn_llm_failure(session, client=client)


def _bang_shell_command(message: str) -> str | None:
    # Explicit `!cmd` shell escape: a deterministic bypass for input the user
    # typed verbatim as a shell command. This is NOT natural-language intent
    # inference — do NOT copy this pattern for bare aliases, regex/keyword
    # matches, or "obvious" natural-language intents. Those must go through the
    # action-agent LLM selecting first-class AgentTools. Engineers have been
    # fired before for reintroducing regex/keyword intent shortcuts here.
    stripped = message.strip()
    if not stripped.startswith("!") or len(stripped) <= 1:
        return None
    cmd = " ".join(stripped[1:].split())
    return f"!{cmd}" if cmd else None


def _slash_tokens(stripped: str) -> tuple[str, list[str]]:
    """Split slash text into a command and arguments, keeping quoted spans whole.

    A quoted argument such as a five-field cron expression must survive as one
    token or the target command sees five stray positionals. Ordinary prose
    after a slash can contain an unbalanced apostrophe that ``shlex`` refuses,
    so fall back to a plain split rather than failing the dispatch.
    """
    try:
        parts = shlex.split(stripped, posix=True)
    except ValueError:
        parts = stripped.split()
    if not parts:
        return stripped, []
    return parts[0], parts[1:]


def _literal_slash_tool_call(message: str, agent_tools: list[Any]) -> ToolCall | None:
    """Deterministic ``slash_invoke`` for input the user typed as a literal ``/command``.

    Like the ``!cmd`` shell escape, this dispatches an *explicit, verbatim* command;
    it is NOT natural-language intent inference (free-form text such as "log me in"
    still goes through the action-agent LLM). Routing the typed command straight to
    the ``slash_invoke`` tool means slash commands keep working when the action-agent
    LLM is unavailable — e.g. a provider with no credit — so users can still run
    ``/login``, ``/onboard``, ``/model``, etc. to recover instead of deadlocking.

    Also accepts schedule affirmatives that ``expand_affirmative_follow_up`` rewrote
    into a leading ``/cron add …`` (after stripping a vendor context prefix).

    Returns ``None`` (so the normal LLM path runs) when the input is not literal
    slash text or when ``slash_invoke`` is not an available tool this turn.
    """
    from platform.harness_ports import strip_message_context_prefix

    _, remainder = strip_message_context_prefix(message)
    stripped = remainder.strip()
    if not stripped.startswith("/"):
        return None
    if not any(getattr(tool, "name", None) == "slash_invoke" for tool in agent_tools):
        return None
    if stripped == "/":
        command, args = "/", list[str]()
    else:
        command, args = _slash_tokens(stripped)
    return ToolCall(
        id="direct_slash_0",
        name="slash_invoke",
        input={"command": command, "args": args},
    )


def _build_action_agent(
    *,
    message: str,
    session: SessionStore,
    agent_tools: list[Any],
    turn_snapshot: TurnSnapshot | None,
    resolved_integrations: dict[str, Any],
    deps: ToolCallingDeps | None,
    tool_hooks: ToolExecutionHooks | None,
    tool_resources: dict[str, Any],
    observer: Any,
) -> ActionTurnPlan:
    """Build the Agent for one action turn; return an ``ActionTurnPlan``.

    Detects the three branches — verbatim ``!shell``, literal ``/slash``, or
    LLM-selected — and picks a matching LLM (deterministic tool-call or hosted
    factory), system prompt, and user-message envelope. The caller only has to
    invoke ``.run()`` and shape the result.
    """
    bang_command = _bang_shell_command(message)
    slash_call = (
        None if bang_command is not None else _literal_slash_tool_call(message, agent_tools)
    )
    # Only LLM-selected turns get a goal reviewer: the verbatim `!shell` and
    # literal `/slash` paths execute exactly one explicit command by design,
    # so "did the agent reach the goal" is not a meaningful question there.
    goal: Goal | None = None
    executed_tool_names: list[str] = []

    if bang_command is not None:
        # Explicit `!` shell escape: dispatch the verbatim text as a shell_run call.
        llm: Any = _StaticToolCallLLM(
            [
                ToolCall(
                    id="direct_shell_0",
                    name="shell_run",
                    input={"command": bang_command},
                )
            ]
        )
        system = "Execute the explicit shell_run tool call."
        user_message = message
    elif slash_call is not None:
        # Explicit literal `/slash`. Dispatch through the same `slash_invoke`
        # AgentTool the LLM would otherwise pick, so typed commands keep working
        # when the action-agent LLM is unavailable.
        llm = _StaticToolCallLLM([slash_call])
        system = "Execute the explicit slash_invoke tool call."
        user_message = message
    else:
        factory = deps.llm_factory if deps is not None and deps.llm_factory else default_llm_factory
        llm = factory()
        envelope = build_action_system_prompt_envelope(
            # No turn plan means no surface is known here; setup facts are
            # omitted rather than guessed (see _setup_state_for_surface).
            turn_snapshot or TurnSnapshot.from_session(message, session, surface=None)
        )
        # Cached half stays byte-identical across turns; ephemeral (conversation,
        # prior-action-facts) rides with the user message so Anthropic's system
        # cache_control breakpoint is not invalidated every turn.
        system = envelope.render_cached()
        user_message = build_action_user_message(message, prefix=envelope.render_ephemeral())
        # Reviewed goal: when the agent concludes after tool work, one LLM
        # check confirms the user's request was carried out; a NOT_REACHED
        # verdict nudges the loop to continue instead of stopping short
        # (e.g. "remove the cron loops" ending after only listing them).
        # The reviewer reads executed tool names from the shared list the
        # event tap below fills, so it can stand down on handoff/dispatch
        # turns whose outcome is not reviewable at conclusion time.
        goal = build_goal_reviewer(llm, message, executed_tool_names)

    # WAL first, observer second: the tool intent must be on disk before
    # any surface side effect reacts to the same event.
    on_runtime_event = with_wal_recording(
        runtime_event_callback_from_observer(observer),
        session=session,
        user_text=message,
    )
    if goal is not None:
        on_runtime_event = tap_executed_tool_names(on_runtime_event, executed_tool_names)

    config = AgentConfig(
        llm=llm,
        system=system,
        tools=tuple(agent_tools),
        resolved_integrations=resolved_integrations,
        max_iterations=_MAX_TOOL_CALLING_ITERATIONS,
        tool_resources=tool_resources,
        tool_hooks=tool_hooks,
        on_runtime_event=on_runtime_event,
        goal=goal,
    )
    return ActionTurnPlan(
        agent=build_agent(config),
        user_message=user_message,
        llm=llm,
        max_iterations=_MAX_TOOL_CALLING_ITERATIONS,
    )


@dataclass(frozen=True)
class _ActionTurnArgs:
    """Internal args for one ``_run_action_turn`` call."""

    output: OutputSink
    tools: ToolProvider
    confirm_fn: ConfirmFn | None = None
    is_tty: bool | None = None
    deps: ToolCallingDeps | None = None
    turn_plan: TurnPlan | None = None
    error_reporter: ErrorReporter | None = None
    tool_hooks: ToolExecutionHooks | None = None


@dataclass(frozen=True)
class ActionTurnRunner:
    """Runs action turns for one surface.

    Where output goes, which tools exist, how errors are reported and how tools
    are hooked all belong to the surface and outlive any single turn, so they are
    given once here instead of being restated on every call.

    Only ``turn_plan``, ``is_tty`` and ``confirm_fn`` change between turns, so
    they stay arguments to :meth:`run`.
    """

    output: OutputSink
    tools: ToolProvider
    deps: ToolCallingDeps | None = None
    error_reporter: ErrorReporter | None = None
    tool_hooks: ToolExecutionHooks | None = None

    def run(
        self,
        message: str,
        session: SessionStore,
        *,
        turn_plan: TurnPlan | None = None,
        is_tty: bool | None = None,
        confirm_fn: ConfirmFn | None = None,
    ) -> ToolCallingTurnResult:
        """Run one action tool-calling turn for ``message`` against ``session``.

        ``turn_plan`` is the turn-wide assembly. Its snapshot builds the
        action-agent system prompt so the prompt reflects turn-start state rather
        than the live (potentially mid-mutation) session, and its resolved
        integrations build the action tools so prompt and tools agree.
        """
        args = _ActionTurnArgs(
            output=self.output,
            tools=self.tools,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            deps=self.deps,
            turn_plan=turn_plan,
            error_reporter=self.error_reporter,
            tool_hooks=self.tool_hooks,
        )
        with component_span("action_turn", session_id=getattr(session, "session_id", None)):
            return _run_action_turn(message, session, args)


@dataclass(frozen=True)
class _TurnCounts:
    """What ran this turn, counted once from history rows and tool results."""

    executed_entries: list[dict[str, Any]]
    executed_count: int
    executed_success_count: int
    generic_success_count: int
    planned_count: int
    handled: bool
    investigation_dispatched: bool
    handoff_contents: tuple[str, ...]


def _compose_response(
    result: Any,
    session: SessionStore,
    counts: _TurnCounts,
) -> tuple[str, list[str], bool]:
    """Build the turn's response text and what to show on screen.

    Returns ``(response_text, display_chunks, use_final_text)``. The two differ
    on purpose: self-recording tools (shell, slash) already printed their own
    output, so the console shows only the closing text, generic tool results and
    any hint. ``response_text`` keeps the history as well, because persistence
    and non-TTY surfaces have nothing else to read.

    Consumes the session's pending outcome hint.
    """
    final_text = str(getattr(result, "final_text", "") or "").strip()
    generic_text = _response_text_from_generic_results(result)
    hint = _pop_turn_outcome_hint(session)
    prefer_tool_response_text = _has_preferred_tool_response_text(result)
    # Self-recording tools (slash/shell/…) already rendered the real output.
    # Drop model closings so they cannot contradict what the user just saw
    # (classic failure: inventing "health check passed" after a failed /health).
    # Exceptions: a multi-step shell/slash chain, whose closing summary is
    # grounded in the output the model observed between steps, and a closing
    # question, which seeks direction instead of restating output.
    # A handoff means the assistant answers this turn, so the action's closing
    # prose would be a second reply to one message ("good morning" twice).
    suppress_final = (
        prefer_tool_response_text
        or bool(counts.handoff_contents)
        or (
            _self_recording_tools_only(result)
            and not _multi_step_grounded_chain(result)
            and not _asks_the_user(final_text)
        )
    )
    final_text_chunk = "" if suppress_final else final_text
    # History entries are already rendered by self-recording tools (shell/slash/…).
    # Console display uses final_text + generic results + hints only so users see
    # github_cli / other registry tools without double-printing shell output.
    # response_text still includes history for persistence / non-TTY surfaces.
    display_chunks = [chunk for chunk in (final_text_chunk, generic_text, hint) if chunk]
    response_chunks = [
        chunk
        for chunk in (
            _response_text_from_history_entries(counts.executed_entries),
            final_text_chunk,
            generic_text,
            hint,
        )
        if chunk
    ]
    # Prefer the agent's closing prose when it looks like a real reply (report /
    # multi-line Markdown). Short one-liners like "done" are common after a
    # single tool call and must not replace tool-derived response_text or get
    # streamed on action-only turns (gateway finalize / cross-surface parity).
    # A tool's explicit ``response_text`` also wins over chatty model closings.
    use_final_text = _is_user_facing_final_text(final_text) and not suppress_final
    response_text = final_text if use_final_text else "\n".join(response_chunks)
    return response_text, display_chunks, use_final_text


def _show_response(
    output: OutputSink,
    *,
    handled: bool,
    final_text: str,
    display_chunks: list[str],
) -> None:
    """Show the turn's answer, or leave a blank line after silent tool work.

    ``final_text`` arrives empty unless the closing message reads like a real
    reply; only then is it streamed as the assistant speaking.
    """
    if handled and final_text:
        output.stream(label="OpenSRE", chunks=iter([final_text]))
        return
    if display_chunks:
        output.print()
        output.render_response_header("assistant")
        # Literal text: the sink decides how to render it safely. The harness
        # must not reach for terminal-markup helpers (agent_harness/AGENTS.md).
        output.print("\n".join(display_chunks))
        return
    if handled:
        output.print()


def _count_turn(result: Any, session: SessionStore, history_start: int) -> _TurnCounts:
    """Count what ran, from the history rows this turn added plus the results."""
    executed_entries = [
        item
        for item in session.history[history_start:]
        if item.get("type") in _EXECUTED_HISTORY_TYPES
    ]
    generic_executed_count, generic_success_count = _generic_tool_result_counts(result)
    # ``assistant_handoff`` runs like a tool but hands back to conversation, so
    # it must not make the turn look like it did something for the user.
    planned_count = sum(1 for tc, _output in result.executed if tc.name != "assistant_handoff")
    return _TurnCounts(
        executed_entries=executed_entries,
        executed_count=len(executed_entries) + generic_executed_count,
        executed_success_count=(
            sum(1 for item in executed_entries if item.get("ok", True)) + generic_success_count
        ),
        generic_success_count=generic_success_count,
        planned_count=planned_count,
        handled=planned_count > 0,
        investigation_dispatched=any(
            tc.name in INVESTIGATION_DISPATCH_TOOL_NAMES for tc, _output in result.executed
        ),
        handoff_contents=tuple(
            content
            for tc, _output in result.executed
            if tc.name == "assistant_handoff"
            for content in (str(public_tool_input(tc.input).get("content", "")).strip(),)
            if content
        ),
    )


def _run_action_turn(
    message: str,
    session: SessionStore,
    args: _ActionTurnArgs,
) -> ToolCallingTurnResult:
    turn_plan = args.turn_plan
    turn_snapshot = turn_plan.snapshot if turn_plan is not None else None
    # Read the turn's resolved integrations once, so the action tools and the
    # AgentConfig are built from the same view (single source, no re-resolve).
    resolved_integrations = _turn_resolved_integrations(session, turn_plan)
    history_start = len(session.history)

    agent_tools = args.tools.action_tools(
        confirm_fn=args.confirm_fn,
        is_tty=args.is_tty,
        resolved_integrations=resolved_integrations,
    )
    tool_resources_provider = getattr(args.tools, "tool_resources", None)
    tool_resources = tool_resources_provider() if callable(tool_resources_provider) else {}
    observer = args.tools.observer(message=message)
    log.debug(
        "action_turn start tools=%s integrations=%s",
        len(agent_tools),
        len(resolved_integrations),
    )

    built: ActionTurnPlan | None = None
    try:
        # LLM selection inside _build_action_agent is inside the try so a factory
        # raise (e.g. provider unavailable) is caught and rendered like a run-loop
        # failure. Agent construction is cheap and stays with it for a single
        # failure boundary.
        built = _build_action_agent(
            message=message,
            session=session,
            agent_tools=agent_tools,
            turn_snapshot=turn_snapshot,
            resolved_integrations=resolved_integrations,
            deps=args.deps,
            tool_hooks=args.tool_hooks,
            tool_resources=tool_resources,
            observer=observer,
        )
        result = run_react_agent_with_telemetry(
            built.agent,
            [{"role": "user", "content": built.user_message}],
            phase="action",
            iteration_cap=built.max_iterations,
            llm=built.llm,
            session=session,
        )
        persist_turn_system_prompt(
            session,
            phase="action_agent",
            system_prompt=result.final_system_prompt,
        )
    except Exception as exc:
        if is_context_length_overflow(str(exc)):
            log.debug("shell action prompt overflow; falling through to assistant", exc_info=True)
            return ToolCallingTurnResult(0, 0, 0, False, False, accounting_status="not_run")

        error_text = str(exc)
        if args.error_reporter is not None:
            args.error_reporter.report(
                exc, context="core.agent_harness.action_driver", expected=True
            )
        llm_client = (
            None if built is None or isinstance(built.llm, _StaticToolCallLLM) else built.llm
        )
        _stage_action_llm_failure(
            message,
            session,
            client=llm_client,
            error_text=error_text,
        )
        _render_tool_calling_error(args.output, error_text)
        _persist_tool_calling_error(session, message, error_text)
        session.record("cli_agent", message, ok=False)
        return ToolCallingTurnResult(
            0, 0, 0, True, True, response_text=error_text, accounting_status="not_run"
        )

    counts = _count_turn(result, session, history_start)
    response_text, display_chunks, use_final_text = _compose_response(result, session, counts)
    # Discovery tools that opt into ``summarize_observation`` (via tool tags)
    # return structured JSON users should not see raw. Stash only those results.
    if (
        response_text.strip()
        and counts.generic_success_count > 0
        and not session.last_command_observation
        and _should_stash_observation(
            result,
            tools_by_name={getattr(t, "name", ""): t for t in agent_tools},
        )
    ):
        session.last_command_observation = response_text
    _show_response(
        args.output,
        handled=counts.handled,
        # use_final_text means the composed text *is* the closing message.
        final_text=response_text if use_final_text else "",
        display_chunks=display_chunks,
    )

    log.debug(
        "action_turn done planned=%s executed=%s handled=%s investigation=%s",
        counts.planned_count,
        counts.executed_count,
        counts.handled,
        counts.investigation_dispatched,
    )
    return ToolCallingTurnResult(
        counts.planned_count,
        counts.executed_count,
        counts.executed_success_count,
        False,
        counts.handled,
        response_text=response_text,
        handoff_contents=counts.handoff_contents,
        investigation_dispatched=counts.investigation_dispatched,
    )


__all__ = [
    "ActionTurnPlan",
    "ActionTurnRunner",
    "SELF_RECORDING_ACTION_TOOL_NAMES",
    "ToolCallingDeps",
]
