"""Action tool-calling turn driver (decoupled from any terminal surface).

Runs one turn through the shared :class:`core.agent.Agent` tool-calling
loop: it assembles the available agent tools (via a :class:`~core.agent_harness.ports.ToolProvider`),
drives the loop while a tool-event observer streams each tool call to the
surface, and summarizes the executed tool calls into a facts-only
:class:`~core.agent_harness.turn_results.ToolCallingTurnResult`.

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
from core.agent_harness.conversation_memory import MAX_CONVERSATION_MESSAGES
from core.agent_harness.ports import (
    ConfirmFn,
    ErrorReporter,
    OutputSink,
    SessionStore,
    ToolProvider,
)
from core.agent_harness.prompts import build_action_system_prompt, build_action_user_message
from core.agent_harness.turn_context import TurnContext
from core.agent_harness.turn_results import ToolCallingTurnResult
from core.events import RuntimeEvent, legacy_callback_payload
from core.llm.types import AgentLLMResponse, ToolCall
from integrations.llm_cli.failure_explain import is_context_length_overflow

log = logging.getLogger(__name__)

# Some hosted tool-calling models emit one tool call per assistant turn even when
# parallel tool calls are enabled. Keep the tool-calling loop bounded, but allow
# the shared AgentTool path to continue through a two-action compound request and
# a final no-tool response.
_MAX_TOOL_CALLING_ITERATIONS = 3
_EXECUTED_HISTORY_TYPES = {
    "slash",
    "shell",
    "alert",
    "synthetic_test",
    "implementation",
    "cli_command",
}


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
    return "\n".join(chunks)


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


def run_agent_turn(
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
) -> ToolCallingTurnResult:
    """Run one action tool-calling turn through the shared agent harness.

    ``turn_ctx`` is the immutable per-turn snapshot assembled at turn start.
    When present it is used to build the action-agent system prompt so the
    prompt reflects turn-start state rather than the live (potentially
    mid-mutation) session.
    """
    history_start = len(session.history)
    agent_tools = tools.action_tools(confirm_fn=confirm_fn, is_tty=is_tty)
    observer = tools.observer(message=message)

    bang_command = _bang_shell_command(message)
    slash_call = (
        None if bang_command is not None else _literal_slash_tool_call(message, agent_tools)
    )
    if bang_command is not None:
        # Explicit `!` shell escape. This is not a general "deterministic command"
        # fast path or regex/keyword intent matcher — it dispatches only input the
        # user typed verbatim as a shell command.
        def llm_factory() -> _StaticToolCallLLM:
            return _StaticToolCallLLM(
                [ToolCall(id="direct_shell_0", name="shell_run", input={"command": bang_command})]
            )

        user_message = message
        system_prompt = "Execute the explicit shell_run tool call."
    elif slash_call is not None:
        # Explicit literal `/slash` command. Dispatch it deterministically through
        # the same `slash_invoke` AgentTool the LLM would otherwise pick, so typed
        # commands run without the action-agent LLM (and keep working when it is
        # unavailable). Natural-language intent is still LLM-selected below.
        slash_tool_call = slash_call

        def llm_factory() -> _StaticToolCallLLM:
            return _StaticToolCallLLM([slash_tool_call])

        user_message = message
        system_prompt = "Execute the explicit slash_invoke tool call."
    else:
        llm_factory = (
            deps.llm_factory if deps is not None and deps.llm_factory else _default_llm_factory
        )
        user_message = build_action_user_message(message)
        effective_ctx = turn_ctx or TurnContext.from_session(message, session)
        system_prompt = build_action_system_prompt(effective_ctx)

    try:

        def on_runtime_event(event: RuntimeEvent) -> None:
            legacy = legacy_callback_payload(event)
            if legacy is not None:
                observer(*legacy)

        result = Agent(
            llm=llm_factory(),
            system=system_prompt,
            tools=agent_tools,
            resolved_integrations={},
            max_iterations=_MAX_TOOL_CALLING_ITERATIONS,
            on_runtime_event=on_runtime_event,
        ).run([{"role": "user", "content": user_message}])
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
    planned_count = sum(1 for tc, _output in result.executed if tc.name != "assistant_handoff")
    handled = planned_count > 0
    response_text = _response_text_from_history_entries(executed_entries)
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
    "ToolCallingDeps",
    "run_agent_turn",
]
