"""LangChain-only adapter for interactive chat models (issue #1358 seam for #1363)."""

from __future__ import annotations

from importlib import import_module
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool

from app.config import DEFAULT_MAX_TOKENS
from app.tools.registered_tool import RegisteredTool
from app.tools.registry import get_registered_tools
from app.types.chat import AssistantTurn, BoundChatModel, ToolCallPayload

_LC_TYPE_TO_ROLE: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
}


def _to_structured_tool(tool: RegisteredTool) -> StructuredTool:
    """Build a StructuredTool from the canonical registered tool runtime."""
    return StructuredTool.from_function(
        func=tool.run,
        name=tool.name,
        description=tool.description,
        return_direct=False,
    )


def structured_chat_tools() -> list[StructuredTool]:
    """Structured tools for ``bind_tools`` on LangChain chat models."""
    return [_to_structured_tool(tool) for tool in get_registered_tools("chat")]


def _build_langchain_chat_model(*, provider: str, model_name: str) -> BaseChatModel:
    """Instantiate provider-specific LangChain chat models (dynamic import)."""
    if provider == "openai":
        openai_module = import_module("langchain_openai")
        chat_openai_cls: Any = openai_module.ChatOpenAI
        return cast(
            BaseChatModel,
            chat_openai_cls(
                model=model_name,
                max_tokens=DEFAULT_MAX_TOKENS,
                streaming=True,
            ),
        )
    if provider == "anthropic":
        anthropic_module = import_module("langchain_anthropic")
        chat_anthropic_cls: Any = anthropic_module.ChatAnthropic
        return cast(
            BaseChatModel,
            chat_anthropic_cls(
                model=model_name,
                max_tokens=DEFAULT_MAX_TOKENS,
                streaming=True,
            ),
        )
    raise ValueError(f"Unsupported chat model provider: {provider}")


def _tool_calls_to_neutral(raw: Any) -> list[ToolCallPayload]:
    out: list[ToolCallPayload] = []
    for tc in raw or []:
        if isinstance(tc, dict):
            tc_id = str(tc.get("id", ""))
            name = str(tc.get("name", ""))
            args = tc.get("args")
            if not isinstance(args, dict):
                args = {}
        else:
            tc_id = str(getattr(tc, "id", "") or "")
            name = str(getattr(tc, "name", "") or "")
            raw_args = getattr(tc, "args", None)
            args = raw_args if isinstance(raw_args, dict) else {}
        out.append(ToolCallPayload(id=tc_id, name=name, args=args))
    return out


def _assistant_message_to_turn(msg: AIMessage) -> AssistantTurn:
    """Convert a LangChain AIMessage into a neutral assistant turn dict."""
    raw_content = msg.content
    if isinstance(raw_content, str):
        text = raw_content
    else:
        text = str(raw_content)
    tool_calls = _tool_calls_to_neutral(getattr(msg, "tool_calls", None))

    turn: AssistantTurn = {"content": text}
    if tool_calls:
        turn["tool_calls"] = tool_calls
    return turn


def lc_message_to_neutral_dict(msg: BaseMessage) -> dict[str, Any]:
    """Serialize a LangChain message to OpenAI-style neutral dicts."""
    if isinstance(msg, SystemMessage):
        return {"role": "system", "content": str(msg.content)}
    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": str(msg.content)}
    if isinstance(msg, AIMessage):
        out: dict[str, Any] = {"role": "assistant", "content": str(msg.content)}
        tool_calls = _tool_calls_to_neutral(getattr(msg, "tool_calls", None))
        if tool_calls:
            out["tool_calls"] = list(tool_calls)
        return out
    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "content": str(msg.content),
            "tool_call_id": str(msg.tool_call_id),
            "name": str(msg.name),
        }
    return {"role": "user", "content": str(getattr(msg, "content", ""))}


def normalize_graph_message_dict(m: dict[str, Any]) -> dict[str, Any]:
    """Ensure a dict has ``role`` (maps legacy LangChain ``type`` if needed)."""
    out = dict(m)
    if "role" not in out and "type" in out:
        out["role"] = _LC_TYPE_TO_ROLE.get(str(out["type"]), "user")
    return out


def messages_to_invocation_dicts(msgs: list[Any]) -> list[dict[str, Any]]:
    """Convert LangGraph ``messages`` reducer entries to neutral dicts."""
    out: list[dict[str, Any]] = []
    for m in msgs:
        if isinstance(m, BaseMessage):
            out.append(lc_message_to_neutral_dict(m))
        elif isinstance(m, dict):
            out.append(normalize_graph_message_dict(m))
        else:
            raise TypeError(f"Unsupported message type for chat invoke: {type(m)!r}")
    return out


def _dict_to_lc_message(raw: dict[str, Any]) -> BaseMessage:
    """Convert a neutral or OpenAI-style dict into a LangChain BaseMessage."""
    msg_dict = normalize_graph_message_dict(raw)
    role = str(msg_dict.get("role") or "user")
    content = msg_dict.get("content", "")
    if not isinstance(content, str):
        content = str(content)

    if role == "system":
        return SystemMessage(content=content)
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        tool_calls = msg_dict.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            formatted: list[dict[str, Any]] = []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                formatted.append(
                    {
                        "name": str(tc.get("name", "")),
                        "id": str(tc.get("id", "")),
                        "args": tc.get("args") if isinstance(tc.get("args"), dict) else {},
                    }
                )
            return AIMessage(content=content, tool_calls=formatted)
        return AIMessage(content=content)
    if role == "tool":
        tc_id = msg_dict.get("tool_call_id")
        if tc_id is None:
            raise ValueError("tool message dict missing tool_call_id")
        name = str(msg_dict.get("name", ""))
        return ToolMessage(content=content, tool_call_id=str(tc_id), name=name)
    return HumanMessage(content=content)


def coerce_messages_for_invocation(msgs: list[Any]) -> list[BaseMessage]:
    """Normalize neutral dicts (or BaseMessage) to LangChain messages."""
    out: list[BaseMessage] = []
    for m in msgs:
        if isinstance(m, BaseMessage):
            out.append(m)
        elif isinstance(m, dict):
            out.append(_dict_to_lc_message(m))
        else:
            raise TypeError(f"Unsupported message type for chat invoke: {type(m)!r}")
    return out


class _LangChainBoundChatWrapper:
    """Wraps a LangChain chat model (plain or tool-bound) behind ``BoundChatModel``."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def invoke(self, messages: list[Any]) -> AssistantTurn:
        lc_messages = coerce_messages_for_invocation(messages)
        response = self._inner.invoke(lc_messages)
        if isinstance(response, AIMessage):
            return _assistant_message_to_turn(response)
        content = str(getattr(response, "content", "") or "")
        turn: AssistantTurn = {"content": content}
        raw_tcs = getattr(response, "tool_calls", None)
        if raw_tcs:
            turn["tool_calls"] = _tool_calls_to_neutral(raw_tcs)
        return turn


def build_bound_chat_model(
    *,
    provider: str,
    model_name: str,
    with_tools: bool,
) -> BoundChatModel:
    """Construct a provider chat model and return the framework-neutral wrapper."""
    base = _build_langchain_chat_model(provider=provider, model_name=model_name)
    if with_tools:
        bound = base.bind_tools(structured_chat_tools())
        return _LangChainBoundChatWrapper(bound)
    return _LangChainBoundChatWrapper(base)
