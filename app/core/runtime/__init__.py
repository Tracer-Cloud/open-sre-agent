"""Shared LLM tool-calling runtime.

Provider-agnostic machinery for running a think → call tools → observe loop:
parallel tool execution, provider-specific message shaping, and context-window
budget enforcement.

Consumers:

* :mod:`app.core.orchestration.node.investigate` — investigation agent loop
* :mod:`app.cli.interactive_shell.chat.tool_gathering` — REPL evidence gathering
"""

from __future__ import annotations

from app.core.runtime.context_budget import (
    context_budget_ceiling_for_model,
    enforce_context_budget,
    estimate_message_tokens,
    trim_lowest_value_tool_pair,
    truncate_content,
)
from app.core.runtime.execution import (
    execute_tools,
    public_tool_input,
    summarise,
    tool_source,
)
from app.core.runtime.loop import LoopEventCallback, ToolLoopResult, run_tool_calling_loop
from app.core.runtime.messages import (
    build_assistant_message,
    build_synthetic_assistant_tool_call_message,
    build_tool_result_messages,
)

__all__ = [
    "LoopEventCallback",
    "ToolLoopResult",
    "build_assistant_message",
    "build_synthetic_assistant_tool_call_message",
    "build_tool_result_messages",
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
