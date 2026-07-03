"""Action tool-calling turn driver (decoupled from any terminal surface).

Runs one turn through the shared :class:`core.agent.Agent` tool-calling
loop: it assembles the available agent tools (via a :class:`~core.agent_harness.ports.ToolProvider`),
drives the loop while a tool-event observer streams each tool call to the
surface, and summarizes the executed tool calls into a facts-only
:class:`~core.agent_harness.models.turn_results.ToolCallingTurnResult`.

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
from core.agent_harness.debug.prompt_trace import persist_turn_system_prompt
from core.agent_harness.models.turn_context import TurnContext
from core.agent_harness.models.turn_results import ToolCallingTurnResult
from core.agent_harness.ports import (
    ConfirmFn,
    ErrorReporter,
    OutputSink,
    SessionStore,
    ToolProvider,
)
from core.agent_harness.prompts import build_action_system_prompt, build_action_user_message
from core.agent_harness.prompts.conversation_memory import MAX_CONVERSATION_MESSAGES
from core.events import runtime_event_callback_from_observer
from core.execution import ToolExecutionHooks, public_tool_input
from core.llm.types import AgentLLMResponse, ToolCall
from integrations.llm_cli.failure_explain import is_context_length_overflow

log = logging.getLogger(__name__)

# Some hosted tool-calling models emit one tool call per assistant turn even when
# parallel tool calls are enabled. Keep the tool-calling loop bounded, but leave
# enough headroom for a *data-dependent* compound request that must run
# sequentially: each step waits for the previous tool's result before the next
# call can be emitted (e.g. "look up the weather and then send it to Slack" =
# shell_run -> observe temperature -> slack_send_message -> final no-tool reply).
# Independent compound turns still fit in a single response; this ceiling exists
# for the producer -> consumer chains plus a couple of intermediate steps.
_MAX_TOOL_CALLING_ITERATIONS = 6
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
    pop_hint = getattr(session, "pop_turn_outcome_hint", None)
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


def _generic_tool_result_counts(result: Any) -> tuple[int, int]:
    generic_results = _generic_tool_results(result)
    executed_count = len(generic_results)
    success_count = sum(
        1
        for _tool_call, tool_result in generic_results
        if not getattr(tool_result, "is_error", False)
    )
    return executed_count, success_count


def _resolved_integrations_for_turn(
    session: SessionStore,
    turn_ctx: TurnContext | None,
) -> dict[str, Any]:
    if turn_ctx is not None and turn_ctx.resolved_integrations:
        return dict(turn_ctx.resolved_integrations)
    return dict(Agent.resolve_integrations(session))


def _persist_tool_calling_error(session: SessionStore, user_text: str, error_text: str) -> None:
    session.cli_agent_messages.append(("user", user_text))
    session.cli_agent_messages.append(("assistant", error_text))
    if len(session.cli_agent_messages) > MAX_CONVERSATION_MESSAGES:
        session.cli_agent_messages[:] = session.cli_agent_messages[-MAX_CONVERSATION_MESSAGES:]


def _render_tool_calling_error(output: OutputSink, message: str) -> None:
    output.print()
    output.render_response_header("assistant")
    output.render_error(message)


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


def _default_llm_factory() -> Any:
    from core.llm import agent_llm_client

    return agent_llm_client.get_agent_llm()


def _build_action_agent(
    *,
    message: str,
    session: SessionStore,
    agent_tools: list[Any],
    turn_ctx: TurnContext | None,
    deps: ToolCallingDeps | None,
    tool_hooks: ToolExecutionHooks | None,
    tool_resources: dict[str, Any],
    observer: Any,
) -> tuple[Agent[Any], str]:
    """Build the Agent for one action turn; return ``(agent, user_message)``.

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
        # Explicit `!` shell escape — dispatches input the user typed verbatim
        # as a shell command. Not a deterministic-command fast path or
        # regex/keyword intent matcher.
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
        factory = (
            deps.llm_factory if deps is not None and deps.llm_factory else _default_llm_factory
        )
        llm = factory()
        system = build_action_system_prompt(turn_ctx or TurnContext.from_session(message, session))
        user_message = build_action_user_message(message)

    config = AgentConfig(
        llm=llm,
        system=system,
        tools=tuple(agent_tools),
        resolved_integrations=_resolved_integrations_for_turn(session, turn_ctx),
        max_iterations=_MAX_TOOL_CALLING_ITERATIONS,
        tool_resources=tool_resources,
        tool_hooks=tool_hooks,
        on_runtime_event=runtime_event_callback_from_observer(observer),
    )
    return build_agent(config), user_message


def run_action_agent_turn(
    message: str,
    session: SessionStore,
    *,
    output: OutputSink,
    tools: ToolProvider,
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    deps: ToolCallingDeps | None = None,
    turn_ctx: TurnContext | None = None,
    error_reporter: ErrorReporter | None = None,
    tool_hooks: ToolExecutionHooks | None = None,
) -> ToolCallingTurnResult:
    """Run one action tool-calling turn through the shared agent harness.

    ``turn_ctx`` is the immutable per-turn snapshot assembled at turn start.
    When present it is used to build the action-agent system prompt so the
    prompt reflects turn-start state rather than the live (potentially
    mid-mutation) session.
    """
    history_start = len(session.history)

    agent_tools = tools.action_tools(confirm_fn=confirm_fn, is_tty=is_tty)
    tool_resources_provider = getattr(tools, "tool_resources", None)
    tool_resources = tool_resources_provider() if callable(tool_resources_provider) else {}
    observer = tools.observer(message=message)

    try:
        # LLM selection inside _build_action_agent is inside the try so a factory
        # raise (e.g. provider unavailable) is caught and rendered like a run-loop
        # failure. Agent construction is cheap and stays with it for a single
        # failure boundary.
        agent, user_message = _build_action_agent(
            message=message,
            session=session,
            agent_tools=agent_tools,
            turn_ctx=turn_ctx,
            deps=deps,
            tool_hooks=tool_hooks,
            tool_resources=tool_resources,
            observer=observer,
        )
        result = agent.run([{"role": "user", "content": user_message}])
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
    response_chunks = [
        chunk
        for chunk in (
            _response_text_from_history_entries(executed_entries),
            _response_text_from_generic_results(result),
            _pop_turn_outcome_hint(session),
        )
        if chunk
    ]
    response_text = "\n".join(response_chunks)
    if handled:
        output.print()

    return ToolCallingTurnResult(
        planned_count,
        executed_count,
        executed_success_count,
        False,
        handled,
        response_text=response_text,
    )


__all__ = [
    "SELF_RECORDING_ACTION_TOOL_NAMES",
    "ToolCallingDeps",
    "run_action_agent_turn",
]
