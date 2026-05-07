"""Direct-SDK chat adapter for interactive chat models (issue #1363).

Replaces the LangChain-backed ``chat_langchain_adapter`` with calls directly
to the ``openai`` and ``anthropic`` SDKs.  The public surface — ``BoundChatModel``,
``AssistantTurn``, ``build_bound_chat_model``, ``messages_to_invocation_dicts`` —
is identical so ``app/nodes/chat.py`` requires zero changes.
"""

from __future__ import annotations

import json
import time
from typing import Any

from anthropic import Anthropic
from anthropic import AuthenticationError as AnthropicAuthError
from openai import AuthenticationError as OpenAIAuthError
from openai import OpenAI

from app.config import DEFAULT_MAX_TOKENS
from app.llm_credentials import resolve_llm_api_key
from app.tools.registered_tool import RegisteredTool
from app.tools.registry import get_registered_tools
from app.types.chat import AssistantTurn, BoundChatModel, ToolCallPayload

# ── Retry / timeout policy (mirror app/services/llm_client.py) ───────────────

_RETRY_INITIAL_BACKOFF_SEC = 1.0
_RETRY_MAX_ATTEMPTS = 3
_CLIENT_TIMEOUT_SEC = 60.0

# ── Role mapping for legacy LC-typed messages in state ───────────────────────

_LC_TYPE_TO_ROLE: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
}


# ── Tool schema builders ──────────────────────────────────────────────────────


def _openai_tool_schema(tool: RegisteredTool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _anthropic_tool_schema(tool: RegisteredTool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def _openai_chat_tools() -> list[dict[str, Any]]:
    return [_openai_tool_schema(t) for t in get_registered_tools("chat")]


def _anthropic_chat_tools() -> list[dict[str, Any]]:
    return [_anthropic_tool_schema(t) for t in get_registered_tools("chat")]


# ── Neutral message dict helpers ─────────────────────────────────────────────


def normalize_graph_message_dict(m: dict[str, Any]) -> dict[str, Any]:
    """Ensure a neutral dict has ``role`` (maps legacy LC ``type`` if needed)."""
    out = dict(m)
    if "role" not in out and "type" in out:
        out["role"] = _LC_TYPE_TO_ROLE.get(str(out["type"]), "user")
    return out


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


def lc_message_to_neutral_dict(msg: Any) -> dict[str, Any]:
    """Convert a LangChain BaseMessage to a neutral role/content dict.

    Kept for the ``messages_to_invocation_dicts`` bridge until the LangGraph
    state no longer contains ``BaseMessage`` objects (#1361 / #1365).
    """
    try:
        from langchain_core.messages import (
            AIMessage,
            BaseMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )
    except ImportError:
        return {"role": "user", "content": str(getattr(msg, "content", ""))}

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
    if isinstance(msg, BaseMessage):
        return {"role": "user", "content": str(getattr(msg, "content", ""))}
    return {"role": "user", "content": str(getattr(msg, "content", ""))}


def messages_to_invocation_dicts(msgs: list[Any]) -> list[dict[str, Any]]:
    """Convert LangGraph ``messages`` reducer entries to neutral dicts.

    Accepts both plain dicts (the new path) and ``BaseMessage`` objects still
    present in LangGraph state until #1361 completes the message-schema migration.
    """
    out: list[dict[str, Any]] = []
    for m in msgs:
        if isinstance(m, dict):
            out.append(normalize_graph_message_dict(m))
        else:
            # BaseMessage or any object with .content — delegate via lazy import
            out.append(lc_message_to_neutral_dict(m))
    return out


# ── OpenAI chat adapter ───────────────────────────────────────────────────────


def _normalize_messages_for_openai(
    msgs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert neutral dicts to the shape OpenAI's chat completions API expects."""
    out: list[dict[str, Any]] = []
    for m in msgs:
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)

        if role == "tool":
            out.append(
                {
                    "role": "tool",
                    "content": content,
                    "tool_call_id": str(m.get("tool_call_id", "")),
                }
            )
            continue

        if role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": content}
            tcs = m.get("tool_calls")
            if tcs:
                entry["tool_calls"] = [
                    {
                        "id": str(tc.get("id", "")),
                        "type": "function",
                        "function": {
                            "name": str(tc.get("name", "")),
                            "arguments": json.dumps(tc.get("args", {})),
                        },
                    }
                    for tc in tcs
                    if isinstance(tc, dict)
                ]
            out.append(entry)
            continue

        out.append({"role": role, "content": content})
    return out


class _OpenAIChatAdapter:
    """Direct ``openai.OpenAI`` implementation of ``BoundChatModel``."""

    def __init__(self, *, model: str, with_tools: bool) -> None:
        self._model = model
        self._with_tools = with_tools
        self._max_tokens = DEFAULT_MAX_TOKENS
        self._api_key: str = ""
        self._client: OpenAI | None = None

    def _ensure_client(self) -> OpenAI:
        api_key = resolve_llm_api_key("OPENAI_API_KEY") or ""
        if not api_key:
            raise RuntimeError(
                "Missing OPENAI_API_KEY. Set it in your environment or .env before running chat."
            )
        if self._client is None or api_key != self._api_key:
            self._api_key = api_key
            self._client = OpenAI(api_key=api_key, timeout=_CLIENT_TIMEOUT_SEC)
        return self._client

    def invoke(self, messages: list[Any]) -> AssistantTurn:
        dicts = messages_to_invocation_dicts(messages)
        normalized = _normalize_messages_for_openai(dicts)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": normalized,
        }
        if self._with_tools:
            tools = _openai_chat_tools()
            if tools:
                kwargs["tools"] = tools

        client = self._ensure_client()
        backoff = _RETRY_INITIAL_BACKOFF_SEC
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                response = client.chat.completions.create(**kwargs)
                break
            except OpenAIAuthError as err:
                raise RuntimeError(
                    "OpenAI authentication failed. Check OPENAI_API_KEY in your environment or .env."
                ) from err
            except Exception as err:
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        "OpenAI API request failed after multiple retries. Try again in a few seconds."
                    ) from err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError("OpenAI invocation failed without a concrete error") from last_err

        if not response.choices:
            raise RuntimeError("OpenAI API returned an empty choices list")

        msg = response.choices[0].message
        content = msg.content or ""
        turn: AssistantTurn = {"content": content}

        raw_tool_calls = getattr(msg, "tool_calls", None)
        if raw_tool_calls:
            parsed: list[ToolCallPayload] = []
            for tc in raw_tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, AttributeError):
                    args = {}
                parsed.append(
                    ToolCallPayload(
                        id=str(tc.id or ""),
                        name=str(tc.function.name or ""),
                        args=args if isinstance(args, dict) else {},
                    )
                )
            if parsed:
                turn["tool_calls"] = parsed

        return turn


# ── Anthropic chat adapter ────────────────────────────────────────────────────


def _split_system_messages(
    msgs: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Extract leading system messages into the Anthropic top-level ``system`` param."""
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in msgs:
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, str):
                system_parts.append(content)
        else:
            rest.append(m)
    return ("\n".join(system_parts) if system_parts else None, rest)


def _normalize_messages_for_anthropic(
    msgs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert neutral dicts to Anthropic's messages format.

    Anthropic tool-result messages use ``role: user`` with a ``tool_result``
    content block, not the OpenAI ``role: tool`` shape.
    """
    out: list[dict[str, Any]] = []
    for m in msgs:
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)

        if role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(m.get("tool_call_id", "")),
                            "content": content,
                        }
                    ],
                }
            )
            continue

        if role == "assistant":
            tcs = m.get("tool_calls")
            if tcs:
                content_blocks: list[dict[str, Any]] = []
                if content:
                    content_blocks.append({"type": "text", "text": content})
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(tc.get("id", "")),
                            "name": str(tc.get("name", "")),
                            "input": tc.get("args", {}),
                        }
                    )
                out.append({"role": "assistant", "content": content_blocks})
                continue

        out.append({"role": role, "content": content})
    return out


class _AnthropicChatAdapter:
    """Direct ``anthropic.Anthropic`` implementation of ``BoundChatModel``."""

    def __init__(self, *, model: str, with_tools: bool) -> None:
        self._model = model
        self._with_tools = with_tools
        self._max_tokens = DEFAULT_MAX_TOKENS
        self._api_key: str = ""
        self._client: Anthropic | None = None

    def _ensure_client(self) -> Anthropic:
        api_key = resolve_llm_api_key("ANTHROPIC_API_KEY") or ""
        if not api_key:
            raise RuntimeError(
                "Missing ANTHROPIC_API_KEY. Set it in your environment or .env before running chat."
            )
        if self._client is None or api_key != self._api_key:
            self._api_key = api_key
            self._client = Anthropic(api_key=api_key, timeout=_CLIENT_TIMEOUT_SEC)
        return self._client

    def invoke(self, messages: list[Any]) -> AssistantTurn:
        dicts = messages_to_invocation_dicts(messages)
        system, non_system = _split_system_messages(dicts)
        normalized = _normalize_messages_for_anthropic(non_system)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": normalized,
        }
        if system:
            kwargs["system"] = system
        if self._with_tools:
            tools = _anthropic_chat_tools()
            if tools:
                kwargs["tools"] = tools

        client = self._ensure_client()
        backoff = _RETRY_INITIAL_BACKOFF_SEC
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                response = client.messages.create(**kwargs)
                break
            except AnthropicAuthError as err:
                raise RuntimeError(
                    "Anthropic authentication failed. Check ANTHROPIC_API_KEY in your environment or .env."
                ) from err
            except Exception as err:
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        "Anthropic API request failed after multiple retries. Try again in a few seconds."
                    ) from err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError("Anthropic invocation failed without a concrete error") from last_err

        text_parts: list[str] = []
        tool_calls: list[ToolCallPayload] = []
        for block in getattr(response, "content", []):
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(str(getattr(block, "text", "")))
            elif block_type == "tool_use":
                raw_input = getattr(block, "input", {})
                tool_calls.append(
                    ToolCallPayload(
                        id=str(getattr(block, "id", "")),
                        name=str(getattr(block, "name", "")),
                        args=raw_input if isinstance(raw_input, dict) else {},
                    )
                )

        turn: AssistantTurn = {"content": "".join(text_parts)}
        if tool_calls:
            turn["tool_calls"] = tool_calls
        return turn


# ── Public factory ────────────────────────────────────────────────────────────


def build_bound_chat_model(
    *,
    provider: str,
    model_name: str,
    with_tools: bool,
) -> BoundChatModel:
    """Construct a direct-SDK provider chat model behind ``BoundChatModel``."""
    if provider == "openai":
        return _OpenAIChatAdapter(model=model_name, with_tools=with_tools)
    if provider == "anthropic":
        return _AnthropicChatAdapter(model=model_name, with_tools=with_tools)
    raise ValueError(f"Unsupported chat model provider: {provider}")
