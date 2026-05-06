# app/types/messages.py
"""Internal message protocol for OpenSRE.

These types describe the message shapes that OpenSRE's LLM client and nodes use
internally. Adapters at the end of this module convert to/from langchain_core.messages
at graph boundaries only.

Do NOT import langchain_core.messages anywhere except:
  - app/pipeline/graph.py  (graph wiring boundary)
  - this module             (adapter functions only)
"""

from __future__ import annotations

from typing import Any, Literal

from typing_extensions import TypedDict


class _SREMessageRequired(TypedDict):
    """Required fields for every SREMessage."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class SREMessage(_SREMessageRequired, total=False):
    """A single message in a conversation, provider-agnostic."""

    tool_calls: list[dict[str, Any]] | None
    tool_call_id: str | None
    name: str | None


# Convenience alias — always use this in function signatures, not list[SREMessage]
SREMessageList = list[SREMessage]


def make_system(content: str) -> SREMessage:
    """Create a system message."""
    return SREMessage(role="system", content=content)


def make_user(content: str) -> SREMessage:
    """Create a user message."""
    return SREMessage(role="user", content=content)


def make_assistant(content: str, tool_calls: list[dict[str, Any]] | None = None) -> SREMessage:
    """Create an assistant message."""
    res = SREMessage(role="assistant", content=content)
    if tool_calls:
        res["tool_calls"] = tool_calls
    return res


def make_tool(content: str, tool_call_id: str, name: str) -> SREMessage:
    """Create a tool message."""
    return SREMessage(role="tool", content=content, tool_call_id=tool_call_id, name=name)


# ---------------------------------------------------------------------------
# Adapters — ONLY used at graph boundaries. Do NOT call these in business logic.
# ---------------------------------------------------------------------------


def to_lc_messages(msgs: SREMessageList) -> list[Any]:
    """Convert internal messages to langchain_core.messages for LangGraph.

    Import is deferred to keep langchain_core out of the module-level namespace.
    This function must only be called at graph wiring time, not in node logic.
    """
    from langchain_core.messages import (  # noqa: PLC0415
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    result: list[Any] = []
    for m in msgs:
        role = m["role"]
        content = m.get("content", "")
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content, tool_calls=m.get("tool_calls") or []))
        elif role == "tool":
            result.append(
                ToolMessage(
                    content=content,
                    tool_call_id=m.get("tool_call_id", ""),
                    name=m.get("name", ""),
                )
            )
    return result


def from_lc_message(msg: Any) -> SREMessage:
    """Convert a langchain_core message to an internal SREMessage.

    Import is deferred to keep langchain_core out of the module-level namespace.
    """
    from langchain_core.messages import (  # noqa: PLC0415
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    if isinstance(msg, dict):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        res = SREMessage(role=role, content=content)
        if "tool_calls" in msg:
            res["tool_calls"] = msg["tool_calls"]
        if "tool_call_id" in msg:
            res["tool_call_id"] = msg["tool_call_id"]
        if "name" in msg:
            res["name"] = msg["name"]
        return res

    if isinstance(msg, SystemMessage):
        role_type: Literal["system", "user", "assistant", "tool"] = "system"
    elif isinstance(msg, HumanMessage):
        role_type = "user"
    elif isinstance(msg, AIMessage):
        role_type = "assistant"
    elif isinstance(msg, ToolMessage):
        role_type = "tool"
    else:
        # Unknown message type — treat as user to avoid false tool-call matches
        role_type = "user"

    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    res = SREMessage(role=role_type, content=content)

    if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
        from typing import cast

        res["tool_calls"] = cast("list[dict[str, Any]]", msg.tool_calls)

    if isinstance(msg, ToolMessage):
        res["tool_call_id"] = msg.tool_call_id
        res["name"] = msg.name

    return res
