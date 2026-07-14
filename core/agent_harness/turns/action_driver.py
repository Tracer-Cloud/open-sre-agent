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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.agent import Agent
from core.agent_harness.agent_builder import AgentConfig, build_agent
from core.agent_harness.llm_resolution import default_llm_factory
from core.agent_harness.ports import (
    ConfirmFn,
    ErrorReporter,
    OutputSink,
    SessionStore,
    ToolProvider,
)
from core.agent_harness.prompts import build_action_system_prompt, build_action_user_message
from core.agent_harness.prompts.conversation_memory import MAX_CONVERSATION_MESSAGES
from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations
from core.agent_harness.turns.turn_plan import TurnPlan
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.events import runtime_event_callback_from_observer
from core.execution import ToolExecutionHooks, public_tool_input
from core.llm.failure_classification import is_context_length_overflow
from core.llm.types import AgentLLMResponse, ToolCall
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


def _response_text_from_generic_results(result: Any) -> str:
    chunks: list[str] = []
    for tool_call, tool_result in _generic_tool_results(result):
        if getattr(tool_result, "is_error", False):
            continue
        content = _content_to_text(getattr(tool_result, "content", ""))
        if content.strip():
            args = public_tool_input(tool_call.input)
            if args:
                chunks.append(
                    f"{tool_call.name} input: {json.dumps(args, ensure_ascii=False, default=str)}"
                    f"\n{tool_call.name} result: {content.strip()}"
                )
            else:
                chunks.append(f"{tool_call.name} result: {content.strip()}")
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
    session.cli_agent_messages.append(("user", user_text))
    session.cli_agent_messages.append(("assistant", error_text))
    if len(session.cli_agent_messages) > MAX_CONVERSATION_MESSAGES:
        session.cli_agent_messages[:] = session.cli_agent_messages[-MAX_CONVERSATION_MESSAGES:]


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


def _literal_slash_tool_call(message: str, agent_tools: list[Any]) -> ToolCall | None:
    """Deterministic ``slash_invoke`` for input the user typed as a literal ``/command``.

    Like the ``!cmd`` shell escape, this dispatches an *explicit, verbatim* command;
    it is NOT natural-language intent inference (free-form text such as "log me in"
    still goes through the action-agent LLM). Routing the typed command straight to
    the ``slash_invoke`` tool means slash commands keep working when the action-agent
    LLM is unavailable — e.g. a provider with no credit — so users can still run
    ``/login``, ``/onboard``, ``/model``, etc. to recover instead of deadlocking.

    Returns ``None`` (so the normal LLM path runs) when the input is not literal
    slash text or when ``slash_invoke`` is not an available tool this turn.
    """
    stripped = message.strip()
    if not stripped.startswith("/"):
        return None
    if not any(getattr(tool, "name", None) == "slash_invoke" for tool in agent_tools):
        return None
    if stripped == "/":
        command, args = "/", []
    else:
        parts = stripped.split()
        command, args = parts[0], parts[1:]
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
        system = build_action_system_prompt(
            turn_snapshot or TurnSnapshot.from_session(message, session)
        )
        user_message = build_action_user_message(message)

    config = AgentConfig(
        llm=llm,
        system=system,
        tools=tuple(agent_tools),
        resolved_integrations=resolved_integrations,
        max_iterations=_MAX_TOOL_CALLING_ITERATIONS,
        tool_resources=tool_resources,
        tool_hooks=tool_hooks,
        on_runtime_event=runtime_event_callback_from_observer(observer),
    )
    return ActionTurnPlan(
        agent=build_agent(config),
        user_message=user_message,
        llm=llm,
        max_iterations=_MAX_TOOL_CALLING_ITERATIONS,
    )


def run_action_agent_turn(
    message: str,
    session: SessionStore,
    *,
    output: OutputSink,
    tools: ToolProvider,
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    deps: ToolCallingDeps | None = None,
    turn_plan: TurnPlan | None = None,
    error_reporter: ErrorReporter | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ToolCallingTurnResult:
    """Run one action tool-calling turn through the shared agent harness.

    ``turn_plan`` is the turn-wide assembly. Its snapshot builds the action-agent
    system prompt so the prompt reflects turn-start state rather than the live
    (potentially mid-mutation) session, and its resolved integrations build the
    action tools so prompt and tools agree.
    """
    with component_span("action_turn", session_id=getattr(session, "session_id", None)):
        return _run_action_agent_turn_body(
            message,
            session,
            output=output,
            tools=tools,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            deps=deps,
            turn_plan=turn_plan,
            error_reporter=error_reporter,
            tool_hooks=tool_hooks,
        )


def _run_action_agent_turn_body(
    message: str,
    session: SessionStore,
    *,
    output: OutputSink,
    tools: ToolProvider,
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    deps: ToolCallingDeps | None = None,
    turn_plan: TurnPlan | None = None,
    error_reporter: ErrorReporter | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ToolCallingTurnResult:
    turn_snapshot = turn_plan.snapshot if turn_plan is not None else None
    # Read the turn's resolved integrations once, so the action tools and the
    # AgentConfig are built from the same view (single source, no re-resolve).
    resolved_integrations = _turn_resolved_integrations(session, turn_plan)
    history_start = len(session.history)

    agent_tools = tools.action_tools(
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        resolved_integrations=resolved_integrations,
    )
    tool_resources_provider = getattr(tools, "tool_resources", None)
    tool_resources = tool_resources_provider() if callable(tool_resources_provider) else {}
    observer = tools.observer(message=message)
    log.debug(
        "action_turn start tools=%s integrations=%s",
        len(agent_tools),
        len(resolved_integrations),
    )

    plan: ActionTurnPlan | None = None
    try:
        # LLM selection inside _build_action_agent is inside the try so a factory
        # raise (e.g. provider unavailable) is caught and rendered like a run-loop
        # failure. Agent construction is cheap and stays with it for a single
        # failure boundary.
        plan = _build_action_agent(
            message=message,
            session=session,
            agent_tools=agent_tools,
            turn_snapshot=turn_snapshot,
            resolved_integrations=resolved_integrations,
            deps=deps,
            tool_hooks=tool_hooks,
            tool_resources=tool_resources,
            observer=observer,
        )
        result = run_react_agent_with_telemetry(
            plan.agent,
            [{"role": "user", "content": plan.user_message}],
            phase="action",
            iteration_cap=plan.max_iterations,
            llm=plan.llm,
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
        if error_reporter is not None:
            error_reporter.report(exc, context="core.agent_harness.action_driver", expected=True)
        llm_client = None if plan is None or isinstance(plan.llm, _StaticToolCallLLM) else plan.llm
        _stage_action_llm_failure(
            message,
            session,
            client=llm_client,
            error_text=error_text,
        )
        _render_tool_calling_error(output, error_text)
        _persist_tool_calling_error(session, message, error_text)
        session.record("cli_agent", message, ok=False)
        return ToolCallingTurnResult(
            0, 0, 0, True, True, response_text=error_text, accounting_status="not_run"
        )

    executed_entries = [
        item
        for item in session.history[history_start:]
        if item.get("type") in _EXECUTED_HISTORY_TYPES
    ]
    executed_count = len(executed_entries)
    executed_success_count = sum(1 for item in executed_entries if item.get("ok", True))
    generic_executed_count, generic_success_count = _generic_tool_result_counts(result)
    executed_count += generic_executed_count
    executed_success_count += generic_success_count
    planned_count = sum(1 for tc, _output in result.executed if tc.name != "assistant_handoff")
    handled = planned_count > 0
    investigation_dispatched = any(
        tc.name in INVESTIGATION_DISPATCH_TOOL_NAMES for tc, _output in result.executed
    )
    handoff_contents = tuple(
        content
        for tc, _output in result.executed
        if tc.name == "assistant_handoff"
        for content in (str(public_tool_input(tc.input).get("content", "")).strip(),)
        if content
    )
    response_chunks = [
        chunk
        for chunk in (
            _response_text_from_history_entries(executed_entries),
            _response_text_from_generic_results(result),
            _pop_turn_outcome_hint(session),
        )
        if chunk
    ]
    final_text = (getattr(result, "final_text", "") or "").strip()
    # Prefer the agent's closing prose when it looks like a real reply (report /
    # multi-line Markdown). Short one-liners like "done" are common after a
    # single tool call and must not replace tool-derived response_text or get
    # streamed on action-only turns (gateway finalize / cross-surface parity).
    use_final_text = _is_user_facing_final_text(final_text)
    response_text = final_text if use_final_text else "\n".join(response_chunks)
    if handled and use_final_text:
        output.stream(label="OpenSRE", chunks=iter([final_text]))
    elif handled:
        output.print()

    log.debug(
        "action_turn done planned=%s executed=%s handled=%s investigation=%s",
        planned_count,
        executed_count,
        handled,
        investigation_dispatched,
    )
    return ToolCallingTurnResult(
        planned_count,
        executed_count,
        executed_success_count,
        False,
        handled,
        response_text=response_text,
        handoff_contents=handoff_contents,
        investigation_dispatched=investigation_dispatched,
    )


__all__ = [
    "ActionTurnPlan",
    "SELF_RECORDING_ACTION_TOOL_NAMES",
    "ToolCallingDeps",
    "run_action_agent_turn",
]
