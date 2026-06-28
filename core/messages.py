"""Runtime-message model and provider conversion helpers.

The shared agent loop owns a provider-agnostic transcript. Provider-specific
message dictionaries are produced only at the LLM invocation boundary.
Compatibility helpers at the bottom keep the investigation loop's legacy dict
path working while call sites migrate to :class:`RuntimeMessage`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

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


def user_runtime_message(content: RuntimeContent, **metadata: Any) -> UserRuntimeMessage:
    return UserRuntimeMessage(content=content, metadata=dict(metadata))


def app_runtime_message(
    app_type: str,
    content: RuntimeContent,
    *,
    include_in_context: bool = True,
    display: bool = True,
    details: Any = None,
    metadata: MessageMetadata | None = None,
) -> AppRuntimeMessage:
    return AppRuntimeMessage(
        app_type=app_type,
        content=content,
        include_in_context=include_in_context,
        display=display,
        details=details,
        metadata=dict(metadata or {}),
    )


def runtime_assistant_message(llm: Any, response: Any) -> AssistantRuntimeMessage:
    provider_payload = build_assistant_message(llm, response)
    return AssistantRuntimeMessage(
        content=getattr(response, "content", "") or "",
        tool_calls=tuple(getattr(response, "tool_calls", ()) or ()),
        provider_payload=provider_payload,
    )


def runtime_synthetic_assistant_tool_call_message(
    llm: Any,
    tool_calls: list[ToolCall],
    *,
    metadata: MessageMetadata | None = None,
) -> AssistantRuntimeMessage:
    return AssistantRuntimeMessage(
        content="",
        tool_calls=tuple(tool_calls),
        provider_payload=build_synthetic_assistant_tool_call_message(llm, tool_calls),
        metadata=dict(metadata or {}),
    )


def runtime_tool_result_message(
    llm: Any,
    tool_calls: list[ToolCall],
    results: list[Any],
    *,
    metadata: MessageMetadata | None = None,
) -> ToolResultRuntimeMessage:
    return ToolResultRuntimeMessage(
        tool_calls=tuple(tool_calls),
        results=tuple(results),
        provider_payloads=tuple(build_tool_result_messages(llm, tool_calls, results)),
        metadata=dict(metadata or {}),
    )


def ensure_runtime_messages(messages: Sequence[RuntimeMessageLike]) -> list[RuntimeMessage]:
    """Normalize caller input into runtime-message objects.

    Legacy provider dictionaries are accepted for compatibility, but ordinary
    user dicts are immediately converted into the internal user-message shape.
    More complex provider dicts are wrapped as assistant/tool observations with
    their provider payload preserved for replay.
    """

    return [_coerce_runtime_message(message) for message in messages]


def _coerce_runtime_message(message: RuntimeMessageLike) -> RuntimeMessage:
    if not isinstance(message, dict):
        return message

    role = message.get("role")
    if role == "user":
        return UserRuntimeMessage(
            content=message.get("content"),
            metadata=_metadata_from_provider_message(message),
        )
    if role == "assistant":
        return AssistantRuntimeMessage(
            content=message.get("content"),
            provider_payload=dict(message),
            metadata=_metadata_from_provider_message(message),
        )
    if role in {"tool", "toolResult", "tool_result"}:
        tool_name = str(message.get("name") or message.get("toolName") or "tool")
        tool_call_id = str(message.get("tool_call_id") or message.get("toolCallId") or tool_name)
        tool_call = ToolCall(id=tool_call_id, name=tool_name, input={})
        return ToolResultRuntimeMessage(
            tool_calls=(tool_call,),
            results=(message.get("content"),),
            provider_payloads=(dict(message),),
            metadata=_metadata_from_provider_message(message),
        )
    if role == "bashExecution":
        return AppRuntimeMessage(
            app_type="bash_execution",
            content=_text_content_blocks(_bash_execution_to_text(message)),
            include_in_context=not _exclude_from_context(message),
            details=dict(message),
            metadata=_metadata_from_provider_message(message),
        )
    if role == "custom":
        return AppRuntimeMessage(
            app_type="custom",
            content=_content_blocks_or_text(message.get("content")),
            include_in_context=not _exclude_from_context(message),
            details=dict(message),
            metadata=_metadata_from_provider_message(message),
        )
    if role == "branchSummary":
        return AppRuntimeMessage(
            app_type="branch_summary",
            content=_text_content_blocks(
                f"{BRANCH_SUMMARY_PREFIX}{message.get('summary') or ''}{BRANCH_SUMMARY_SUFFIX}"
            ),
            include_in_context=not _exclude_from_context(message),
            details=dict(message),
            metadata=_metadata_from_provider_message(message),
        )
    if role == "compactionSummary":
        return AppRuntimeMessage(
            app_type="compaction_summary",
            content=_text_content_blocks(
                f"{COMPACTION_SUMMARY_PREFIX}{message.get('summary') or ''}"
                f"{COMPACTION_SUMMARY_SUFFIX}"
            ),
            include_in_context=not _exclude_from_context(message),
            details=dict(message),
            metadata=_metadata_from_provider_message(message),
        )
    return AppRuntimeMessage(
        app_type="provider_message",
        content=json.dumps(message, default=str),
        include_in_context=False,
        details=dict(message),
        metadata=_metadata_from_provider_message(message),
    )


def _metadata_from_provider_message(message: ProviderMessage) -> MessageMetadata:
    return {key: value for key, value in message.items() if key.startswith("_opensre_")}


def _exclude_from_context(message: ProviderMessage) -> bool:
    return bool(message.get("excludeFromContext") or message.get("exclude_from_context"))


def _text_content_blocks(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}]


def _content_blocks_or_text(content: Any) -> RuntimeContent:
    if isinstance(content, str):
        return _text_content_blocks(content)
    if content is None:
        return _text_content_blocks("")
    if isinstance(content, list) and all(isinstance(item, dict) for item in content):
        return [dict(item) for item in content]
    return _text_content_blocks(json.dumps(content, default=str))


def _bash_execution_to_text(message: ProviderMessage) -> str:
    content = message.get("content")
    if isinstance(content, str) and not _has_bash_execution_parts(message):
        return content

    lines: list[str] = []
    command = message.get("command") or message.get("cmd")
    cwd = message.get("cwd")
    exit_code = message.get("exitCode", message.get("exit_code"))
    stdout = message.get("stdout")
    stderr = message.get("stderr")
    output = message.get("output")

    if command:
        lines.append(f"$ {command}")
    if cwd:
        lines.append(f"cwd: {cwd}")
    if exit_code is not None:
        lines.append(f"exit code: {exit_code}")
    if stdout:
        lines.append(f"stdout:\n{stdout}")
    if stderr:
        lines.append(f"stderr:\n{stderr}")
    if output and output != stdout:
        lines.append(f"output:\n{output}")
    if content and all(content != value for value in (stdout, stderr, output)):
        lines.append(_stringify_content_section("content", content))

    if lines:
        return "\n\n".join(lines)

    payload = {
        key: value
        for key, value in message.items()
        if key not in {"role", "excludeFromContext", "exclude_from_context"}
    }
    return json.dumps(payload, default=str)


def _has_bash_execution_parts(message: ProviderMessage) -> bool:
    return any(
        key in message
        for key in ("command", "cmd", "cwd", "exitCode", "exit_code", "stdout", "stderr", "output")
    )


def _stringify_content_section(label: str, content: Any) -> str:
    if isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, default=str)
    return f"{label}:\n{text}"


def convert_to_llm_messages(llm: Any, messages: Sequence[RuntimeMessage]) -> list[ProviderMessage]:
    """Render runtime messages into provider-compatible message dictionaries."""

    provider_messages: list[ProviderMessage] = []
    for message in messages:
        provider_messages.extend(_provider_messages_for_runtime_message(llm, message))
    return provider_messages


def _provider_messages_for_runtime_message(
    llm: Any,
    message: RuntimeMessage,
) -> list[ProviderMessage]:
    if isinstance(message, UserRuntimeMessage):
        return [{"role": "user", "content": message.content}]
    if isinstance(message, AssistantRuntimeMessage):
        if message.provider_payload is not None:
            return [dict(message.provider_payload)]
        return [llm.build_assistant_message(message.content or "", list(message.tool_calls))]
    if isinstance(message, ToolResultRuntimeMessage):
        if message.provider_payloads:
            return [dict(payload) for payload in message.provider_payloads]
        return build_tool_result_messages(llm, list(message.tool_calls), list(message.results))
    if isinstance(message, AppRuntimeMessage):
        if not message.include_in_context:
            return []
        return [{"role": "user", "content": _provider_content_for_app_message(llm, message)}]
    return []


def _provider_content_for_app_message(llm: Any, message: AppRuntimeMessage) -> RuntimeContent:
    from core.llm.agent_llm_client import BedrockConverseAgentClient

    if isinstance(llm, BedrockConverseAgentClient):
        return _to_converse_text_blocks(message.content)
    return message.content


def _to_converse_text_blocks(content: RuntimeContent) -> RuntimeContent:
    if not isinstance(content, list):
        return content

    converted: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "text" and "text" in block:
            converted.append({"text": str(block["text"])})
        else:
            converted.append(dict(block))
    return converted


def build_synthetic_assistant_tool_call_message(
    llm: Any,
    tool_calls: list[ToolCall],
) -> ProviderMessage:
    """Build an assistant message that looks like the LLM requested these tool calls.

    This lets us inject pre-seeded tool results into the conversation in a format
    the LLM client already understands, without adding special-case handling.
    """
    from core.llm.agent_llm_client import (
        AnthropicAgentClient,
        BedrockConverseAgentClient,
        CLIBackedAgentClient,
        OpenAIAgentClient,
    )

    if isinstance(llm, BedrockConverseAgentClient):
        from core.llm.bedrock_converse import build_assistant_tool_use_message

        return cast("ProviderMessage", build_assistant_tool_use_message(tool_calls))

    if isinstance(llm, AnthropicAgentClient):
        content = [
            {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }
            for tc in tool_calls
        ]
        return {"role": "assistant", "content": content}

    if isinstance(llm, OpenAIAgentClient):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                }
                for tc in tool_calls
            ],
        }

    if isinstance(llm, CLIBackedAgentClient):
        return cast("ProviderMessage", llm.build_assistant_message("", tool_calls))

    # Fallback: plain text summary
    names = ", ".join(tc.name for tc in tool_calls)
    return {"role": "assistant", "content": f"I will start by querying: {names}"}


def build_assistant_message(llm: Any, response: Any) -> ProviderMessage:
    from core.llm.agent_llm_client import AnthropicAgentClient, BedrockConverseAgentClient

    if isinstance(llm, (AnthropicAgentClient, BedrockConverseAgentClient)):
        return cast("ProviderMessage", llm.build_assistant_message(response.raw_content))
    # Use raw_content when set — preserves provider-specific fields such as
    # Gemini's thought_signature that must be echoed back in the next request.
    if response.raw_content is not None:
        return response.raw_content  # type: ignore[no-any-return]
    result: dict[str, Any] = llm.build_assistant_message(response.content, response.tool_calls)
    return result


def build_tool_result_messages(
    llm: Any,
    tool_calls: list[ToolCall],
    results: list[Any],
) -> list[ProviderMessage]:
    from core.llm.agent_llm_client import AnthropicAgentClient, OpenAIAgentClient

    if isinstance(llm, AnthropicAgentClient):
        return [cast("ProviderMessage", llm.build_tool_result_message(tool_calls, results))]
    if isinstance(llm, OpenAIAgentClient):
        return cast("list[ProviderMessage]", llm.build_tool_result_messages(tool_calls, results))
    return [cast("ProviderMessage", llm.build_tool_result_message(tool_calls, results))]
