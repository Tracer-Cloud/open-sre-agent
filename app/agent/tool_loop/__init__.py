"""Shared tool-calling ReAct primitives.

This package owns the provider-agnostic machinery for running a "think → call
tools → observe" loop against the registered tool set: parallel tool execution,
provider-specific assistant/tool-result message shaping, and the context-window
budget enforcement that keeps long loops under each model's prompt limit.

Two consumers build on top of it:

* :mod:`app.agent.stages.investigate` — the investigation agent layers evidence
  collection, seed calls, and diagnosis parsing on top of these helpers and its
  own loop orchestration.
* the interactive shell's tool-gathering pass — uses :func:`run_tool_calling_loop`
  to let the REPL assistant pull live data from the *same* registered tools the
  investigation uses before composing a conversational answer.

Keeping the loop primitives here (rather than private to ``investigation.py``)
means both surfaces share one implementation of the subtle, well-tested context
budgeting and provider message shaping.
"""

from __future__ import annotations

from app.agent.tool_loop.context_budget import (
    _context_budget_ceiling_for_model,
    _enforce_context_budget,
    _estimate_message_tokens,
    _trim_oldest_tool_pair,
    _truncate_content,
)
from app.agent.tool_loop.execution import (
    _public_tool_input,
    _run_parallel,
    _summarise,
    _tool_source,
)
from app.agent.tool_loop.loop import AgentEventCallback, ToolLoopResult, run_tool_calling_loop
from app.agent.tool_loop.messages import (
    _build_assistant_msg,
    _build_synthetic_assistant_tool_call_msg,
    _build_tool_result_messages,
)

__all__ = [
    "AgentEventCallback",
    "ToolLoopResult",
    "_build_assistant_msg",
    "_build_synthetic_assistant_tool_call_msg",
    "_build_tool_result_messages",
    "_context_budget_ceiling_for_model",
    "_enforce_context_budget",
    "_estimate_message_tokens",
    "_public_tool_input",
    "_run_parallel",
    "_summarise",
    "_tool_source",
    "_trim_oldest_tool_pair",
    "_truncate_content",
    "run_tool_calling_loop",
]
