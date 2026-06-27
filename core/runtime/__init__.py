"""Shared LLM tool-calling runtime.

Provider-agnostic machinery for running a think → call tools → observe loop:
parallel tool execution, provider-specific message shaping, and context-window
budget enforcement.

The top-level primitive is :class:`~core.runtime.agent.Agent`. Surfaces that
previously called ``run_tool_calling_loop`` should instantiate ``Agent``
directly and call ``.run(initial_messages)``.
"""

from __future__ import annotations

from core.runtime.agent import Agent, AgentRunResult, LoopEventCallback, ToolLoopResult
from core.runtime.context_budget import (
    context_budget_ceiling_for_model,
    enforce_context_budget,
    estimate_message_tokens,
    trim_lowest_value_tool_pair,
    truncate_content,
)
from core.runtime.execution import (
    execute_tools,
    public_tool_input,
    summarise,
    tool_source,
)
from core.runtime.llm_invoke_errors import LLMInvokeFailure, classify_llm_invoke_failure
from core.runtime.messages import (
    build_assistant_message,
    build_synthetic_assistant_tool_call_message,
    build_tool_result_messages,
)
from core.runtime.types import AgentTool, AgentToolContext, AgentToolExecutor, RuntimeTool

__all__ = [
    "Agent",
    "AgentRunResult",
    "AgentTool",
    "AgentToolContext",
    "AgentToolExecutor",
    "LoopEventCallback",
    "LLMInvokeFailure",
    "RuntimeTool",
    "ToolLoopResult",
    "build_assistant_message",
    "build_synthetic_assistant_tool_call_message",
    "build_tool_result_messages",
    "classify_llm_invoke_failure",
    "context_budget_ceiling_for_model",
    "enforce_context_budget",
    "estimate_message_tokens",
    "execute_tools",
    "public_tool_input",
    "summarise",
    "tool_source",
    "trim_lowest_value_tool_pair",
    "truncate_content",
]
