"""Runtime-message model and provider conversion helpers.

The shared agent loop owns a provider-agnostic transcript.  Provider-specific
message dictionaries are produced only at the LLM invocation boundary via
:class:`MessageMapper`.
"""

from __future__ import annotations

from core.messages.message_mapper import MessageMapper
from core.messages.runtime_message_types import (
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
from core.messages.transcript import extract_last_assistant_text

__all__ = [
    "AppRuntimeMessage",
    "AssistantRuntimeMessage",
    "MessageMapper",
    "MessageMetadata",
    "ProviderMessage",
    "RuntimeContent",
    "RuntimeMessage",
    "RuntimeMessageLike",
    "ToolResultRuntimeMessage",
    "UserRuntimeMessage",
    "extract_last_assistant_text",
]
