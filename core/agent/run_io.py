"""Input and output data types for the ReAct loop.

``AgentRunInput`` is the resolved per-run input ``Agent.run`` assembles and
hands to ``run_react_loop``; ``AgentRunResult`` is what the loop returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.execution import ToolExecutionResult
from core.llm.types import ToolCall
from core.messages import RuntimeMessage
from core.types import RuntimeTool


@dataclass
class AgentRunResult:
    """Outcome of :func:`core.agent.react_loop.run_react_loop` (returned as-is by ``Agent.run``).

    ``messages`` is the full conversation, ``final_text`` is the assistant's
    last no-tool-call turn, ``executed`` is the historical ordered list of raw
    tool payloads, and ``tool_results`` contains the structured runtime results.
    """

    messages: list[RuntimeMessage]
    final_text: str
    executed: list[tuple[ToolCall, Any]] = field(default_factory=list)
    tool_results: list[tuple[ToolCall, ToolExecutionResult]] = field(default_factory=list)
    terminated_by_tool: bool = False
    hit_iteration_cap: bool = False
    final_system_prompt: str = ""
    """System prompt sent to the LLM on the last request (post-hook), for debugging."""


@dataclass
class AgentRunInput[RuntimeToolT: RuntimeTool]:
    """Resolved, per-run inputs the loop needs — assembled once by ``Agent.run``."""

    llm: Any
    system: str
    tools: list[RuntimeToolT]
    resolved: dict[str, Any]
    tool_resources: dict[str, Any]
    max_iterations: int
    messages: list[RuntimeMessage]


__all__ = ["AgentRunInput", "AgentRunResult"]
