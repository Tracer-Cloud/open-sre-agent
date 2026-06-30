"""Compatibility adapters from legacy provider dictionaries to runtime messages."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from core.llm.types import ToolCall
from core.messages.runtime_message_types import (
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
