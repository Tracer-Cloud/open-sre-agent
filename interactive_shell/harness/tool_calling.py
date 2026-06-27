"""Interactive-shell tool-calling turn execution (action-agent tool loop).

Runs one shell turn through the shared :class:`core.runtime.agent.Agent`
tool-calling loop: it assembles the available agent tools, drives the loop while
``ActionRenderObserver`` streams each tool call to the terminal, and summarizes
the executed tool calls into a facts-only :class:`ToolCallingTurnResult`.

Accounting/analytics for the turn live in
:mod:`interactive_shell.runtime.core.turn_accounting`; this module emits none itself.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.markup import escape

from core.runtime.agent import Agent
from core.runtime.llm.agent_llm_client import AgentLLMResponse, ToolCall
from integrations.llm_cli.failure_explain import is_context_length_overflow
from interactive_shell.harness.agent_context import AgentContext
from interactive_shell.harness.llm_context import (
    build_action_system_prompt,
    build_action_user_message,
)
from interactive_shell.harness.llm_context.session import ReplSession
from interactive_shell.runtime.core.turn_accounting import ToolCallingTurnResult
from interactive_shell.tools.tool_contracts import ToolContext
from interactive_shell.tools.tool_registry import REGISTRY
from interactive_shell.ui.action_rendering import ActionRenderObserver
from interactive_shell.ui.streaming import render_response_header
from interactive_shell.utils.error_handling.exception_reporting import report_exception

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


def _persist_tool_calling_error(session: ReplSession, user_text: str, error_text: str) -> None:
    session.agent.record_turn(user_text, error_text)


def _render_tool_calling_error(console: Console, message: str) -> None:
    console.print()
    render_response_header(console, "assistant")
    console.print(f"[yellow]{escape(message)}[/]")


def _bang_shell_command(message: str) -> str | None:
    # The only deterministic action bypass allowed in this module is the explicit
    # `!cmd` shell escape. Do NOT copy this pattern for `/slash` commands, bare
    # aliases, regex/keyword matches, or "obvious" natural-language intents.
    # Those must go through the action-agent LLM selecting first-class AgentTools.
    # Engineers have been fired before for reintroducing slash/regex shortcuts here.
    stripped = message.strip()
    if not stripped.startswith("!") or len(stripped) <= 1:
        return None
    cmd = " ".join(stripped[1:].split())
    return f"!{cmd}" if cmd else None


def _default_llm_factory() -> Any:
    from core.runtime.llm import agent_llm_client

    return agent_llm_client.get_agent_llm()


def run_tool_calling_turn(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    deps: ToolCallingDeps | None = None,
    agent_ctx: AgentContext | None = None,
) -> ToolCallingTurnResult:
    """Run one shell tool-calling turn through the shared agent harness.

    ``agent_ctx`` is the immutable per-prompt snapshot assembled at prompt start
    in ``run_agent_prompt``. When present it is used to build the
    action-agent system prompt so the prompt reflects prompt-start state rather
    than the live (potentially mid-mutation) session.
    """
    history_start = len(session.history)
    ctx = ToolContext(
        session=session,
        console=console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        action_already_listed=True,
    )
    tools = REGISTRY.agent_tools_for_context(ctx)
    observer = ActionRenderObserver(session=session, console=console, message=message)

    bang_command = _bang_shell_command(message)
    if bang_command is not None:
        # This is intentionally limited to the `!` shell escape. It is not a
        # general "deterministic command" fast path. In particular, do not add
        # `deterministic_command_text`, slash-command parsing, or regex intent
        # matching here. Slash execution still belongs to the `slash_invoke`
        # AgentTool selected by the action agent.
        def llm_factory() -> _StaticToolCallLLM:
            return _StaticToolCallLLM(
                [ToolCall(id="direct_shell_0", name="shell_run", input={"command": bang_command})]
            )

        user_message = message
        system_prompt = "Execute the explicit shell_run tool call."
    else:
        llm_factory = (
            deps.llm_factory if deps is not None and deps.llm_factory else _default_llm_factory
        )
        user_message = build_action_user_message(message)
        effective_ctx = agent_ctx or AgentContext.from_session(message, session)
        system_prompt = build_action_system_prompt(effective_ctx)

    try:
        result = Agent(
            llm=llm_factory(),
            system=system_prompt,
            tools=tools,
            resolved_integrations={},
            max_iterations=_MAX_TOOL_CALLING_ITERATIONS,
            on_event=observer,
        ).run([{"role": "user", "content": user_message}])
    except Exception as exc:
        if is_context_length_overflow(str(exc)):
            log.debug("shell action prompt overflow; falling through to assistant", exc_info=True)
            return ToolCallingTurnResult(0, 0, 0, False, False, accounting_status="not_run")

        error_text = str(exc)
        report_exception(exc, context="interactive_shell.action_agent", expected=True)
        _render_tool_calling_error(console, error_text)
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
        console.print()

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
    "run_tool_calling_turn",
]
