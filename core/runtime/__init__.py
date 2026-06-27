"""Shared Pi-style agent runtime."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "Agent": ("core.runtime.agent", "Agent"),
    "AgentContext": ("core.runtime.types", "AgentContext"),
    "AgentEventCallback": ("core.runtime.events", "AgentEventCallback"),
    "AgentEventKind": ("core.runtime.events", "AgentEventKind"),
    "AgentHarness": ("core.runtime.harness", "AgentHarness"),
    "AgentHarnessContext": ("core.runtime.types", "AgentHarnessContext"),
    "AgentLoopResult": ("core.runtime.types", "AgentLoopResult"),
    "AgentMessage": ("core.runtime.types", "AgentMessage"),
    "AgentSessionStore": ("core.runtime.types", "AgentSessionStore"),
    "AgentTool": ("core.runtime.types", "AgentTool"),
    "AgentToolContext": ("core.runtime.types", "AgentToolContext"),
    "AgentToolExecutor": ("core.runtime.types", "AgentToolExecutor"),
    "IntegrationProvider": ("core.runtime.types", "IntegrationProvider"),
    "LLMInvokeFailure": ("core.runtime.llm_invoke_errors", "LLMInvokeFailure"),
    "LlmFactory": ("core.runtime.types", "LlmFactory"),
    "PendingMessageQueue": ("core.runtime.agent", "PendingMessageQueue"),
    "RuntimeTool": ("core.runtime.types", "RuntimeTool"),
    "SystemPromptProvider": ("core.runtime.types", "SystemPromptProvider"),
    "ToolProvider": ("core.runtime.types", "ToolProvider"),
    "build_assistant_message": ("core.runtime.agent_messages", "build_assistant_message"),
    "build_synthetic_assistant_tool_call_message": (
        "core.runtime.agent_messages",
        "build_synthetic_assistant_tool_call_message",
    ),
    "build_tool_result_messages": ("core.runtime.agent_messages", "build_tool_result_messages"),
    "classify_llm_invoke_failure": (
        "core.runtime.llm_invoke_errors",
        "classify_llm_invoke_failure",
    ),
    "context_budget_ceiling_for_model": (
        "core.runtime.context_budget",
        "context_budget_ceiling_for_model",
    ),
    "enforce_context_budget": ("core.runtime.context_budget", "enforce_context_budget"),
    "estimate_message_tokens": ("core.runtime.context_budget", "estimate_message_tokens"),
    "execute_tools": ("core.runtime.tool_execution", "execute_tools"),
    "public_tool_input": ("core.runtime.tool_execution", "public_tool_input"),
    "run_agent_loop": ("core.runtime.agent_loop", "run_agent_loop"),
    "summarise": ("core.runtime.tool_execution", "summarise"),
    "tool_source": ("core.runtime.tool_execution", "tool_source"),
    "trim_lowest_value_tool_pair": (
        "core.runtime.context_budget",
        "trim_lowest_value_tool_pair",
    ),
    "truncate_content": ("core.runtime.context_budget", "truncate_content"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
