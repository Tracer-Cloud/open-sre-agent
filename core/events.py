"""Typed event contract for the shared agent runtime."""

# ruff: noqa: UP040

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

# CodeQL's explicit-export query does not recognize Python 3.12 ``type``
# statements in __all__, so keep these exported aliases as TypeAlias assignments.
RuntimeEventType: TypeAlias = Literal[
    "agent_start",
    "turn_start",
    "message_start",
    "message_update",
    "provider_request_start",
    "provider_request_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "turn_end",
    "agent_end",
]
RuntimeEventKind: TypeAlias = RuntimeEventType


@dataclass(frozen=True)
class AgentStartEvent:
    type: Literal["agent_start"] = "agent_start"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnStartEvent:
    iteration: int
    type: Literal["turn_start"] = "turn_start"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageStartEvent:
    message: Any
    iteration: int | None = None
    type: Literal["message_start"] = "message_start"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageUpdateEvent:
    message: Any
    delta: str | None = None
    iteration: int | None = None
    type: Literal["message_update"] = "message_update"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRequestStartEvent:
    iteration: int
    message_count: int
    type: Literal["provider_request_start"] = "provider_request_start"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRequestEndEvent:
    iteration: int
    has_tool_calls: bool
    type: Literal["provider_request_end"] = "provider_request_end"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecutionStartEvent:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    iteration: int
    type: Literal["tool_execution_start"] = "tool_execution_start"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecutionUpdateEvent:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    partial_result: Any
    iteration: int
    type: Literal["tool_execution_update"] = "tool_execution_update"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecutionEndEvent:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    result: Any
    is_error: bool
    iteration: int
    type: Literal["tool_execution_end"] = "tool_execution_end"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnEndEvent:
    iteration: int
    message: Any
    tool_results: tuple[Any, ...] = ()
    type: Literal["turn_end"] = "turn_end"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentEndEvent:
    messages: tuple[Any, ...] = ()
    type: Literal["agent_end"] = "agent_end"
    data: dict[str, Any] = field(default_factory=dict)


RuntimeEvent: TypeAlias = (
    AgentStartEvent
    | TurnStartEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | ProviderRequestStartEvent
    | ProviderRequestEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
    | TurnEndEvent
    | AgentEndEvent
)
RuntimeEventCallback: TypeAlias = Callable[[RuntimeEvent], None]
LegacyLoopEventCallback: TypeAlias = Callable[[str, dict[str, Any]], None]
LegacyRuntimeEventCallback: TypeAlias = LegacyLoopEventCallback


def tool_result_is_error(result: Any) -> bool:
    return isinstance(result, dict) and "error" in result


def runtime_event_from_legacy(kind: str, data: dict[str, Any]) -> RuntimeEvent | None:
    """Convert a pre-event-contract callback payload to a typed event when possible."""
    payload = dict(data)
    if kind == "agent_start":
        return AgentStartEvent(data=payload)
    if kind == "llm_start":
        return TurnStartEvent(iteration=int(payload.get("iteration", 0)), data=payload)
    if kind == "provider_request_start":
        return ProviderRequestStartEvent(
            iteration=int(payload.get("iteration", 0)),
            message_count=int(payload.get("message_count", 0)),
            data=payload,
        )
    if kind == "provider_request_end":
        return ProviderRequestEndEvent(
            iteration=int(payload.get("iteration", 0)),
            has_tool_calls=bool(payload.get("has_tool_calls", False)),
            data=payload,
        )
    if kind == "tool_start":
        args = payload.get("input")
        return ToolExecutionStartEvent(
            tool_call_id=str(payload.get("id") or payload.get("tool_call_id") or ""),
            tool_name=str(payload.get("name") or payload.get("tool_name") or "tool"),
            args=dict(args) if isinstance(args, dict) else {},
            iteration=int(payload.get("iteration", -1)),
            data=payload,
        )
    if kind == "tool_update":
        args = payload.get("input")
        return ToolExecutionUpdateEvent(
            tool_call_id=str(payload.get("id") or payload.get("tool_call_id") or ""),
            tool_name=str(payload.get("name") or payload.get("tool_name") or "tool"),
            args=dict(args) if isinstance(args, dict) else {},
            partial_result=payload.get("update"),
            iteration=int(payload.get("iteration", -1)),
            data=payload,
        )
    if kind == "tool_end":
        args = payload.get("input")
        output = payload.get("output")
        return ToolExecutionEndEvent(
            tool_call_id=str(payload.get("id") or payload.get("tool_call_id") or ""),
            tool_name=str(payload.get("name") or payload.get("tool_name") or "tool"),
            args=dict(args) if isinstance(args, dict) else {},
            result=output,
            is_error=tool_result_is_error(output),
            iteration=int(payload.get("iteration", -1)),
            data=payload,
        )
    if kind == "agent_end":
        return AgentEndEvent(data=payload)
    return None


def legacy_callback_payload(event: RuntimeEvent) -> tuple[str, dict[str, Any]] | None:
    """Map a typed runtime event onto the old ``(kind, data)`` callback shape."""
    if isinstance(event, AgentStartEvent):
        return "agent_start", dict(event.data)
    if isinstance(event, TurnStartEvent):
        return "llm_start", {"iteration": event.iteration, **event.data}
    if isinstance(event, ToolExecutionStartEvent):
        return (
            "tool_start",
            {
                "id": event.tool_call_id,
                "name": event.tool_name,
                "input": event.args,
                **event.data,
            },
        )
    if isinstance(event, ToolExecutionUpdateEvent):
        return (
            "tool_update",
            {
                "id": event.tool_call_id,
                "name": event.tool_name,
                "input": event.args,
                "update": event.partial_result,
                **event.data,
            },
        )
    if isinstance(event, ToolExecutionEndEvent):
        return (
            "tool_end",
            {
                "id": event.tool_call_id,
                "name": event.tool_name,
                "input": event.args,
                "output": event.result,
                **event.data,
            },
        )
    if isinstance(event, AgentEndEvent):
        return "agent_end", dict(event.data)
    return None


__all__ = [
    "AgentEndEvent",
    "AgentStartEvent",
    "LegacyLoopEventCallback",
    "LegacyRuntimeEventCallback",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ProviderRequestEndEvent",
    "ProviderRequestStartEvent",
    "RuntimeEvent",
    "RuntimeEventCallback",
    "RuntimeEventKind",
    "RuntimeEventType",
    "ToolExecutionEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
    "legacy_callback_payload",
    "runtime_event_from_legacy",
    "tool_result_is_error",
]
