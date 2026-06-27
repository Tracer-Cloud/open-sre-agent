"""Stateful ReAct agent — the shared primitive for all tool-calling surfaces."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.runtime.context_budget import (
    context_budget_ceiling_for_model,
    enforce_context_budget,
)
from core.runtime.execution import execute_tools, public_tool_input
from core.runtime.llm.agent_llm_client import ToolCall
from core.runtime.messages import build_assistant_message, build_tool_result_messages
from core.runtime.types import RuntimeTool
from platform.observability.tool_trace import redact_sensitive

logger = logging.getLogger(__name__)

# Callback type: called with (event_kind, data_dict) during the agent loop.
# event_kind values: "tool_start", "tool_end", "llm_start", "agent_start", "agent_end"
LoopEventCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class AgentRunResult:
    """Outcome of :meth:`Agent.run`.

    ``messages`` is the full conversation, ``final_text`` is the assistant's
    last no-tool-call turn (empty when the loop hit the iteration cap), and
    ``executed`` is the ordered list of ``(tool_call, output)`` pairs run
    during the loop.
    """

    messages: list[dict[str, Any]]
    final_text: str
    executed: list[tuple[ToolCall, Any]] = field(default_factory=list)
    hit_iteration_cap: bool = False


# Backward-compat alias — callers that still reference ToolLoopResult compile unchanged.
ToolLoopResult = AgentRunResult


class Agent[RuntimeToolT: RuntimeTool]:
    """Stateful, configurable ReAct agent.

    Owns the think → call-tools → observe loop and exposes hook methods so
    subclasses can customise stopping logic and tool filtering without
    re-implementing the loop::

        agent = Agent(llm=llm, system=prompt, tools=tools,
                      resolved_integrations=resolved, max_iterations=8)
        result = agent.run([{"role": "user", "content": text}])

    Hook methods to override in subclasses:

    * :meth:`_should_accept_conclusion` — decide when the LLM may stop
    * :meth:`_filter_tools` — narrow the tool list the LLM sees
    """

    def __init__(
        self,
        *,
        llm: Any,
        system: str,
        tools: Sequence[RuntimeToolT],
        resolved_integrations: dict[str, Any],
        max_iterations: int,
        on_event: LoopEventCallback | None = None,
    ) -> None:
        self._llm = llm
        self._system = system
        self._tools = list(tools)
        self._resolved = resolved_integrations
        self._max_iterations = max_iterations
        self._on_event = on_event

    def run(self, initial_messages: list[dict[str, Any]]) -> AgentRunResult:
        """Run the think → call-tools → observe loop and return its outcome."""
        messages = list(initial_messages)
        runtime_tools = list(self._filter_tools(self._tools))
        tool_schemas = self._llm.tool_schemas(runtime_tools)
        ceiling = context_budget_ceiling_for_model(getattr(self._llm, "_model", None))
        executed: list[tuple[ToolCall, Any]] = []
        final_text = ""
        hit_cap = True

        for iteration in range(self._max_iterations):
            self._emit("llm_start", {"iteration": iteration})
            enforce_context_budget(
                messages, system=self._system, tools=tool_schemas, ceiling=ceiling
            )
            response = self._llm.invoke(messages, system=self._system, tools=tool_schemas)
            messages.append(build_assistant_message(self._llm, response))

            if not response.has_tool_calls:
                accept, nudge = self._should_accept_conclusion(
                    evidence_count=len(executed), iteration=iteration
                )
                if accept:
                    final_text = response.content or ""
                    hit_cap = False
                    break
                if nudge is None:
                    raise ValueError(
                        f"{type(self).__name__}._should_accept_conclusion returned "
                        "(False, None) — a nudge string is required when rejecting "
                        "the conclusion, otherwise the LLM will loop on an unchanged "
                        "message history until max_iterations."
                    )
                messages.append({"role": "user", "content": nudge})
                continue

            for tc in response.tool_calls:
                self._emit(
                    "tool_start",
                    {"id": tc.id, "name": tc.name, "input": public_tool_input(tc.input)},
                )

            results = execute_tools(response.tool_calls, runtime_tools, self._resolved)
            messages.extend(build_tool_result_messages(self._llm, response.tool_calls, results))

            for tc, output in zip(response.tool_calls, results):
                executed.append((tc, output))
                self._emit(
                    "tool_end",
                    {"id": tc.id, "name": tc.name, "output": redact_sensitive(output)},
                )

        return AgentRunResult(
            messages=messages,
            final_text=final_text,
            executed=executed,
            hit_iteration_cap=hit_cap,
        )

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,  # noqa: ARG002 — used by overrides
        iteration: int,  # noqa: ARG002 — used by overrides
    ) -> tuple[bool, str | None]:
        """Hook: decide what to do when the LLM stops requesting tools.

        Return ``(True, None)`` to accept the conclusion and end the loop.
        Return ``(False, nudge_text)`` to inject a user message and continue.
        """
        return True, None

    def _filter_tools(self, tools: list[RuntimeToolT]) -> list[RuntimeToolT]:
        """Hook: narrow the tool list the agent will see."""
        return tools

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        if self._on_event is not None:
            try:
                self._on_event(kind, data)
            except Exception:  # noqa: BLE001 — event rendering must never break the loop
                logger.debug("[runtime] on_event(%s) raised; ignoring", kind, exc_info=True)
