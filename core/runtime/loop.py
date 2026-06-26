"""Generic bounded think → call tools → observe loop."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.runtime.context_budget import (
    context_budget_ceiling_for_model,
    enforce_context_budget,
)
from core.runtime.execution import execute_tools, public_tool_input
from core.runtime.messages import build_assistant_message, build_tool_result_messages
from platform.observability.tool_trace import redact_sensitive
from services.agent_llm_client import ToolCall
from tools.registered_tool import RegisteredTool

logger = logging.getLogger(__name__)

# Callback type: called with (event_kind, data_dict) during the agent loop.
# event_kind values: "tool_start", "tool_end", "llm_start", "agent_start", "agent_end"
LoopEventCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class ToolLoopResult:
    """Outcome of :func:`run_tool_calling_loop`.

    ``messages`` is the full conversation (mutated in place and returned for
    convenience), ``final_text`` is the assistant's last no-tool-call turn (the
    conversational answer, empty when the loop hit the iteration cap), and
    ``executed`` is the ordered list of ``(tool_call, output)`` pairs run during
    the loop.
    """

    messages: list[dict[str, Any]]
    final_text: str
    executed: list[tuple[ToolCall, Any]] = field(default_factory=list)
    hit_iteration_cap: bool = False


def run_tool_calling_loop(
    *,
    llm: Any,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[RegisteredTool],
    resolved_integrations: dict[str, Any],
    max_iterations: int,
    on_event: LoopEventCallback | None = None,
) -> ToolLoopResult:
    """Run a generic think → call-tools → observe loop and return its outcome.

    Unlike :class:`core.orchestration.node.investigate.ConnectedInvestigationAgent`, this is
    a plain conversational loop: it does not seed tool calls, collect evidence,
    or parse a diagnosis. It exists so non-investigation surfaces (currently the
    interactive shell's tool-gathering pass) can call the *same* registered tools
    the investigation uses, with the same provider message shaping and context
    budgeting.

    ``on_event`` mirrors the investigation agent's callback contract so callers
    can render ``tool_start`` / ``tool_end`` activity live.
    """

    def _emit(kind: str, data: dict[str, Any]) -> None:
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

    for iteration in range(max_iterations):
        _emit("llm_start", {"iteration": iteration})
        enforce_context_budget(messages, system=system, tools=tool_schemas, ceiling=ceiling)
        response = llm.invoke(messages, system=system, tools=tool_schemas)
        messages.append(build_assistant_message(llm, response))

        if not response.has_tool_calls:
            final_text = response.content or ""
            hit_cap = False
            break

        for tc in response.tool_calls:
            _emit(
                "tool_start", {"id": tc.id, "name": tc.name, "input": public_tool_input(tc.input)}
            )

        results = execute_tools(response.tool_calls, tools, resolved_integrations)
        messages.extend(build_tool_result_messages(llm, response.tool_calls, results))

        for tc, output in zip(response.tool_calls, results):
            executed.append((tc, output))
            _emit(
                "tool_end",
                {"id": tc.id, "name": tc.name, "output": redact_sensitive(output)},
            )

    return ToolLoopResult(
        messages=messages,
        final_text=final_text,
        executed=executed,
        hit_iteration_cap=hit_cap,
    )
