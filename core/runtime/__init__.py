"""Shared Pi-style agent runtime.

Provider-agnostic machinery for running the three runtime layers:

* ``agent_loop`` — pure think -> call tools -> observe loop
* ``agent`` — stateful transcript/run wrapper
* ``harness`` — session-aware model/tool/resource binding

Consumers:

* :mod:`core.orchestration.node.investigate` — investigation agent loop
* :mod:`interactive_shell.chat.tool_gathering` — REPL evidence gathering
"""

from __future__ import annotations

from core.runtime.agent import Agent, PendingMessageQueue
from core.runtime.agent_loop import run_agent_loop
from core.runtime.agent_messages import (
    build_assistant_message,
    build_synthetic_assistant_tool_call_message,
    build_tool_result_messages,
)
from core.runtime.context_budget import (
    context_budget_ceiling_for_model,
    enforce_context_budget,
    estimate_message_tokens,
    trim_lowest_value_tool_pair,
    truncate_content,
)
from core.runtime.events import AgentEventCallback, AgentEventKind
from core.runtime.harness import AgentHarness
from core.runtime.llm_invoke_errors import LLMInvokeFailure, classify_llm_invoke_failure
from core.runtime.tool_execution import (
    execute_tools,
    public_tool_input,
    summarise,
    tool_source,
)
from core.runtime.types import (
    AgentContext,
    AgentHarnessContext,
    AgentLoopResult,
    AgentMessage,
    AgentSessionStore,
)

__all__ = [
    "Agent",
    "AgentContext",
    "AgentEventCallback",
    "AgentEventKind",
    "AgentHarness",
    "AgentHarnessContext",
    "AgentLoopResult",
    "AgentMessage",
    "AgentSessionStore",
    "LLMInvokeFailure",
    "PendingMessageQueue",
    "build_assistant_message",
    "build_synthetic_assistant_tool_call_message",
    "build_tool_result_messages",
    "classify_llm_invoke_failure",
    "context_budget_ceiling_for_model",
    "enforce_context_budget",
    "estimate_message_tokens",
    "execute_tools",
    "public_tool_input",
    "run_agent_loop",
    "summarise",
    "tool_source",
    "trim_lowest_value_tool_pair",
    "truncate_content",
]
