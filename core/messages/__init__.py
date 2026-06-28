"""Runtime-message model and provider conversion helpers.

The shared agent loop owns a provider-agnostic transcript. Provider-specific
message dictionaries are produced only at the LLM invocation boundary.
Compatibility helpers keep the investigation loop's legacy dict path working
while call sites migrate to :class:`RuntimeMessage`.
"""

from __future__ import annotations

from core.messages.coercion import ensure_runtime_messages
from core.messages.factories import (
    app_runtime_message,
    runtime_assistant_message,
    runtime_synthetic_assistant_tool_call_message,
    runtime_tool_result_message,
    user_runtime_message,
)
from core.messages.models import (
    BRANCH_SUMMARY_PREFIX,
    BRANCH_SUMMARY_SUFFIX,
    COMPACTION_SUMMARY_PREFIX,
    COMPACTION_SUMMARY_SUFFIX,
    AppRuntimeMessage,
    AssistantRuntimeMessage,
    MessageMetadata,
    ProviderMessage,
    RuntimeContent,
    RuntimeMessage,
    RuntimeMessageLike,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)
from core.messages.provider_conversion import (
    build_assistant_message,
    build_synthetic_assistant_tool_call_message,
    build_tool_result_messages,
    convert_to_llm_messages,
)

__all__ = [
    "BRANCH_SUMMARY_PREFIX",
    "BRANCH_SUMMARY_SUFFIX",
    "COMPACTION_SUMMARY_PREFIX",
    "COMPACTION_SUMMARY_SUFFIX",
    "AppRuntimeMessage",
    "AssistantRuntimeMessage",
    "MessageMetadata",
    "ProviderMessage",
    "RuntimeContent",
    "RuntimeMessage",
    "RuntimeMessageLike",
    "ToolResultRuntimeMessage",
    "UserRuntimeMessage",
    "app_runtime_message",
    "build_assistant_message",
    "build_synthetic_assistant_tool_call_message",
    "build_tool_result_messages",
    "convert_to_llm_messages",
    "ensure_runtime_messages",
    "runtime_assistant_message",
    "runtime_synthetic_assistant_tool_call_message",
    "runtime_tool_result_message",
    "user_runtime_message",
]
