"""Pure bounded think -> call tools -> observe loop."""

from __future__ import annotations

import logging
from typing import Any

from core.runtime.agent_messages import build_assistant_message, build_tool_result_messages
from core.runtime.context_budget import (
    context_budget_ceiling_for_model,
    enforce_context_budget,
)
from core.runtime.events import AgentEventCallback, AgentEventKind
from core.runtime.llm.agent_llm_client import ToolCall
from core.runtime.tool_execution import execute_tools, public_tool_input
from core.runtime.types import AgentLoopResult, RuntimeTool
from platform.observability.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)


def run_agent_loop(
    *,
    llm: Any,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[RuntimeTool],
    resolved_integrations: dict[str, Any],
    max_iterations: int,
    on_event: AgentEventCallback | None = None,
) -> AgentLoopResult:
    """Run a generic think -> call-tools -> observe loop and return its outcome.

    Unlike :class:`core.orchestration.node.investigate.ConnectedInvestigationAgent`, this is
    a plain conversational loop: it does not seed tool calls, collect evidence,
    or parse a diagnosis. It exists so non-investigation surfaces (currently the
    interactive shell's tool-gathering pass) can call the *same* registered tools
    the investigation uses, with the same provider message shaping and context
    budgeting.

    ``on_event`` mirrors the investigation agent's callback contract so callers
    can render ``tool_start`` / ``tool_end`` activity live.
    """

    def _emit(kind: AgentEventKind, data: dict[str, Any]) -> None:
        if on_event is not None:
            try:
                on_event(kind, data)
            except Exception:  # noqa: BLE001 — event rendering must never break the loop
                logger.debug("[runtime] on_event(%s) raised; ignoring", kind, exc_info=True)

    tool_schemas = llm.tool_schemas(tools)
    ceiling = context_budget_ceiling_for_model(getattr(llm, "_model", None))
    executed: list[tuple[ToolCall, Any]] = []
    final_text = ""
    hit_cap = True

    _emit("agent_start", {"message_count": len(messages), "tool_count": len(tools)})
    for iteration in range(max_iterations):
        _emit("turn_start", {"iteration": iteration})
        _emit("llm_start", {"iteration": iteration})
        enforce_context_budget(messages, system=system, tools=tool_schemas, ceiling=ceiling)
        response = llm.invoke(messages, system=system, tools=tool_schemas)
        assistant_message = build_assistant_message(llm, response)
        _emit("message_start", {"iteration": iteration, "message": assistant_message})
        messages.append(assistant_message)
        _emit("message_end", {"iteration": iteration, "message": assistant_message})

        if not response.has_tool_calls:
            final_text = response.content or ""
            hit_cap = False
            _emit(
                "turn_end",
                {"iteration": iteration, "tool_count": 0, "stop_reason": "final"},
            )
            break

        for tc in response.tool_calls:
            _emit(
                "tool_start", {"id": tc.id, "name": tc.name, "input": public_tool_input(tc.input)}
            )

        results = execute_tools(response.tool_calls, tools, resolved_integrations)
        tool_result_messages = build_tool_result_messages(llm, response.tool_calls, results)
        for message in tool_result_messages:
            _emit("message_start", {"iteration": iteration, "message": message})
            messages.append(message)
            _emit("message_end", {"iteration": iteration, "message": message})

        for tc, output in zip(response.tool_calls, results):
            executed.append((tc, output))
            _emit(
                "tool_end",
                {"id": tc.id, "name": tc.name, "output": redact_sensitive(output)},
            )
        _emit(
            "turn_end",
            {
                "iteration": iteration,
                "tool_count": len(response.tool_calls),
                "stop_reason": "tool_calls",
            },
        )

    _emit(
        "agent_end",
        {
            "message_count": len(messages),
            "executed_count": len(executed),
            "hit_iteration_cap": hit_cap,
        },
    )
    return AgentLoopResult(
        messages=messages,
        final_text=final_text,
        executed=executed,
        hit_iteration_cap=hit_cap,
    )


__all__ = ["run_agent_loop"]
