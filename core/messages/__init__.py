"""Runtime-message model and provider conversion helpers.

The shared agent loop owns a provider-agnostic transcript.  Provider-specific
message dictionaries are produced only at the LLM invocation boundary via
:class:`MessageFormatter`.
"""

from __future__ import annotations

from core.messages.message_formatter import MessageFormatter
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

__all__ = [
    "AppRuntimeMessage",
    "AssistantRuntimeMessage",
    "MessageFormatter",
    "MessageMetadata",
    "ProviderMessage",
    "RuntimeContent",
    "RuntimeMessage",
    "RuntimeMessageLike",
    "ToolResultRuntimeMessage",
    "UserRuntimeMessage",
]
