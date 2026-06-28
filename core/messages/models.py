"""Provider-agnostic runtime message data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core.llm.types import ToolCall

type MessageMetadata = dict[str, Any]
type ProviderMessage = dict[str, Any]
type RuntimeContent = str | list[dict[str, Any]] | None


@dataclass(frozen=True)
class UserRuntimeMessage:
    """User-visible runtime transcript entry."""

    content: RuntimeContent
    metadata: MessageMetadata = field(default_factory=dict)
    role: Literal["user"] = "user"


@dataclass(frozen=True)
class AssistantRuntimeMessage:
    """Assistant turn retained in runtime shape.

    ``provider_payload`` is optional provider continuity data. It is kept out of
    general app/session metadata and replayed only by provider adapters.
    """

    content: RuntimeContent
    tool_calls: tuple[ToolCall, ...] = ()
    provider_payload: ProviderMessage | None = None
    metadata: MessageMetadata = field(default_factory=dict)
    role: Literal["assistant"] = "assistant"


@dataclass(frozen=True)
class ToolResultRuntimeMessage:
    """Tool-observation entry for one assistant tool-call batch."""

    tool_calls: tuple[ToolCall, ...]
    results: tuple[Any, ...]
    provider_payloads: tuple[ProviderMessage, ...] = ()
    metadata: MessageMetadata = field(default_factory=dict)
    role: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True)
class AppRuntimeMessage:
    """App/session metadata that may optionally be made visible to the model."""

    app_type: str
    content: RuntimeContent
    include_in_context: bool = True
    display: bool = True
    details: Any = None
    metadata: MessageMetadata = field(default_factory=dict)
    role: Literal["app"] = "app"


type RuntimeMessage = (
    UserRuntimeMessage | AssistantRuntimeMessage | ToolResultRuntimeMessage | AppRuntimeMessage
)
type RuntimeMessageLike = RuntimeMessage | ProviderMessage

BRANCH_SUMMARY_PREFIX = "<branch_summary>\n"
BRANCH_SUMMARY_SUFFIX = "\n</branch_summary>"
COMPACTION_SUMMARY_PREFIX = "<compaction_summary>\n"
COMPACTION_SUMMARY_SUFFIX = "\n</compaction_summary>"
