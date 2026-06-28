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
from core.runtime.events import (
    AgentEndEvent,
    AgentStartEvent,
    LegacyLoopEventCallback,
    LegacyRuntimeEventCallback,
    MessageStartEvent,
    MessageUpdateEvent,
    ProviderRequestEndEvent,
    ProviderRequestStartEvent,
    RuntimeEvent,
    RuntimeEventCallback,
    RuntimeEventKind,
    RuntimeEventType,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
    legacy_callback_payload,
    runtime_event_from_legacy,
)
from core.runtime.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool_calls,
    execute_tools,
    public_tool_input,
    summarise,
    tool_source,
)
from core.runtime.llm_invoke_errors import LLMInvokeFailure, classify_llm_invoke_failure
from core.runtime.messages import (
    AppRuntimeMessage,
    AssistantRuntimeMessage,
    RuntimeMessage,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
    build_assistant_message,
    build_synthetic_assistant_tool_call_message,
    build_tool_result_messages,
    convert_to_llm_messages,
    ensure_runtime_messages,
    runtime_assistant_message,
    runtime_synthetic_assistant_tool_call_message,
    runtime_tool_result_message,
    user_runtime_message,
)
from core.runtime.provider import ProviderHooks, ProviderRequest, resolve_llm_api_key
from core.runtime.types import (
    AgentTool,
    AgentToolContext,
    AgentToolExecutor,
    RuntimeTool,
    ToolExecutionMode,
)

__all__ = [
    "Agent",
    "AgentEndEvent",
    "AgentRunResult",
    "AgentStartEvent",
    "AgentTool",
    "AgentToolContext",
    "AgentToolExecutor",
    "AppRuntimeMessage",
    "AssistantRuntimeMessage",
    "BeforeToolCallResult",
    "LLMInvokeFailure",
    "LegacyLoopEventCallback",
    "LegacyRuntimeEventCallback",
    "LoopEventCallback",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ProviderHooks",
    "ProviderRequest",
    "ProviderRequestEndEvent",
    "ProviderRequestStartEvent",
    "RuntimeEvent",
    "RuntimeEventCallback",
    "RuntimeEventKind",
    "RuntimeEventType",
    "RuntimeMessage",
    "RuntimeTool",
    "ToolExecutionEndEvent",
    "ToolExecutionHooks",
    "ToolExecutionMode",
    "ToolExecutionPatch",
    "ToolExecutionRequest",
    "ToolExecutionResult",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "ToolLoopResult",
    "ToolResultRuntimeMessage",
    "TurnEndEvent",
    "TurnStartEvent",
    "UserRuntimeMessage",
    "build_assistant_message",
    "build_synthetic_assistant_tool_call_message",
    "build_tool_result_messages",
    "classify_llm_invoke_failure",
    "context_budget_ceiling_for_model",
    "convert_to_llm_messages",
    "enforce_context_budget",
    "estimate_message_tokens",
    "execute_tool_calls",
    "execute_tools",
    "ensure_runtime_messages",
    "legacy_callback_payload",
    "public_tool_input",
    "resolve_llm_api_key",
    "runtime_assistant_message",
    "runtime_event_from_legacy",
    "runtime_synthetic_assistant_tool_call_message",
    "runtime_tool_result_message",
    "summarise",
    "tool_source",
    "trim_lowest_value_tool_pair",
    "truncate_content",
    "user_runtime_message",
]
