"""Shared LLM tool-calling runtime.

Provider-agnostic machinery for running a think → call tools → observe loop:
parallel tool execution, provider-specific message shaping, and context-window
budget enforcement.

The top-level primitive is :class:`~core.agent.Agent`. Surfaces that
previously called ``run_tool_calling_loop`` should instantiate ``Agent``
directly and call ``.run(initial_messages)``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "Agent": "core.agent",
    "AgentRunResult": "core.agent",
    "LoopEventCallback": "core.agent",
    "ToolLoopResult": "core.agent",
    "context_budget_ceiling_for_model": "core.context_budget",
    "enforce_context_budget": "core.context_budget",
    "estimate_message_tokens": "core.context_budget",
    "trim_lowest_value_tool_pair": "core.context_budget",
    "truncate_content": "core.context_budget",
    "AgentEndEvent": "core.events",
    "AgentStartEvent": "core.events",
    "LegacyLoopEventCallback": "core.events",
    "LegacyRuntimeEventCallback": "core.events",
    "MessageStartEvent": "core.events",
    "MessageUpdateEvent": "core.events",
    "ProviderRequestEndEvent": "core.events",
    "ProviderRequestStartEvent": "core.events",
    "RuntimeEvent": "core.events",
    "RuntimeEventCallback": "core.events",
    "RuntimeEventKind": "core.events",
    "RuntimeEventType": "core.events",
    "ToolExecutionEndEvent": "core.events",
    "ToolExecutionStartEvent": "core.events",
    "ToolExecutionUpdateEvent": "core.events",
    "TurnEndEvent": "core.events",
    "TurnStartEvent": "core.events",
    "legacy_callback_payload": "core.events",
    "runtime_event_from_legacy": "core.events",
    "BeforeToolCallResult": "core.execution",
    "ToolExecutionHooks": "core.execution",
    "ToolExecutionPatch": "core.execution",
    "ToolExecutionRequest": "core.execution",
    "ToolExecutionResult": "core.execution",
    "execute_tool_calls": "core.execution",
    "execute_tools": "core.execution",
    "public_tool_input": "core.execution",
    "summarise": "core.execution",
    "tool_source": "core.execution",
    "LLMInvokeFailure": "core.llm_invoke_errors",
    "classify_llm_invoke_failure": "core.llm_invoke_errors",
    "AppRuntimeMessage": "core.messages",
    "AssistantRuntimeMessage": "core.messages",
    "RuntimeMessage": "core.messages",
    "ToolResultRuntimeMessage": "core.messages",
    "UserRuntimeMessage": "core.messages",
    "build_assistant_message": "core.messages",
    "build_synthetic_assistant_tool_call_message": "core.messages",
    "build_tool_result_messages": "core.messages",
    "convert_to_llm_messages": "core.messages",
    "ensure_runtime_messages": "core.messages",
    "runtime_assistant_message": "core.messages",
    "runtime_synthetic_assistant_tool_call_message": "core.messages",
    "runtime_tool_result_message": "core.messages",
    "user_runtime_message": "core.messages",
    "ProviderHooks": "core.provider",
    "ProviderRequest": "core.provider",
    "resolve_llm_api_key": "core.provider",
    "AgentTool": "core.types",
    "AgentToolContext": "core.types",
    "AgentToolExecutor": "core.types",
    "RuntimeTool": "core.types",
    "ToolExecutionMode": "core.types",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


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
