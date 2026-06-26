"""Shared LLM tool-calling runtime.

Provider-agnostic machinery for running a think → call tools → observe loop:
parallel tool execution, provider-specific message shaping, and context-window
budget enforcement.

Consumers:

* :mod:`core.orchestration.node.investigate` — investigation agent loop
* :mod:`cli.interactive_shell.chat.tool_gathering` — REPL evidence gathering
"""

from __future__ import annotations

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
from core.runtime.loop import LoopEventCallback, ToolLoopResult, run_tool_calling_loop
from core.runtime.messages import (
    build_assistant_message,
    build_synthetic_assistant_tool_call_message,
    build_tool_result_messages,
)

__all__ = [
    "LoopEventCallback",
    "LLMInvokeFailure",
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
    "run_tool_calling_loop",
    "summarise",
    "tool_source",
    "trim_lowest_value_tool_pair",
    "truncate_content",
]
