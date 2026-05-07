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

from app.config import DEFAULT_MAX_TOKENS
from app.llm_credentials import resolve_llm_api_key
from app.tools.registered_tool import RegisteredTool
from app.tools.registry import get_registered_tools
from app.types.chat import AssistantTurn, BoundChatModel, ToolCallPayload

# ── Retry / timeout policy (mirror app/services/llm_client.py) ───────────────

_RETRY_INITIAL_BACKOFF_SEC = 1.0
_RETRY_MAX_ATTEMPTS = 3
_CLIENT_TIMEOUT_SEC = 60.0

# Suffix for non-leading system lines folded into the prior user turn (Anthropic).
_NON_LEADING_SYSTEM_MARK = "[system]"


def _openai_chat_completions_with_retry(client: Any, kwargs: dict[str, Any]) -> Any:
    """Call OpenAI chat completions with retry; raises ``RuntimeError`` on final failure."""
    from openai import AuthenticationError as OpenAIAuthError

    backoff = _RETRY_INITIAL_BACKOFF_SEC
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            return client.chat.completions.create(**kwargs)
        except OpenAIAuthError as err:
            raise RuntimeError(
                "OpenAI authentication failed. Check OPENAI_API_KEY in your environment or .env."
            ) from err
        except Exception as err:
            if attempt == _RETRY_MAX_ATTEMPTS - 1:
                raise RuntimeError(
                    "OpenAI API request failed after multiple retries. Try again in a few seconds."
                ) from err
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError("OpenAI chat completions retry completed without return or raise")


def _anthropic_messages_create_with_retry(client: Any, kwargs: dict[str, Any]) -> Any:
    """Call Anthropic ``messages.create`` with retry."""
    from anthropic import AuthenticationError as AnthropicAuthError

    backoff = _RETRY_INITIAL_BACKOFF_SEC
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            return client.messages.create(**kwargs)
        except AnthropicAuthError as err:
            raise RuntimeError(
                "Anthropic authentication failed. Check ANTHROPIC_API_KEY in your environment or .env."
            ) from err
        except Exception as err:
            if attempt == _RETRY_MAX_ATTEMPTS - 1:
                raise RuntimeError(
                    "Anthropic API request failed after multiple retries. Try again in a few seconds."
                ) from err
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError("Anthropic messages.create retry completed without return or raise")


# ── Role mapping for legacy LC-typed messages in state ───────────────────────

_LC_TYPE_TO_ROLE: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
}


def _legacy_message_to_dict_without_langchain(msg: Any) -> dict[str, Any]:
    """Map an LC-shaped message when ``langchain_core`` is unavailable."""
    content = str(getattr(msg, "content", ""))
    t = getattr(msg, "type", None)
    if isinstance(t, str):
        role = _LC_TYPE_TO_ROLE.get(t, "user")
        return {"role": role, "content": content}
    cn = type(msg).__name__
    class_map = {
        "AIMessage": "assistant",
        "HumanMessage": "user",
        "SystemMessage": "system",
        "ToolMessage": "tool",
    }
    role = class_map.get(cn, "user")
    return {"role": role, "content": content}


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
        return _legacy_message_to_dict_without_langchain(msg)

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
        t = getattr(msg, "type", None)
        if isinstance(t, str):
            role = _LC_TYPE_TO_ROLE.get(t, "user")
        else:
            role = "user"
        return {"role": role, "content": str(getattr(msg, "content", ""))}
    return _legacy_message_to_dict_without_langchain(msg)


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
            name = m.get("name")
            tool_entry: dict[str, Any] = {
                "role": "tool",
                "content": content,
                "tool_call_id": str(m.get("tool_call_id", "")),
            }
            if name is not None and str(name) != "":
                tool_entry["name"] = str(name)
            out.append(tool_entry)
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
        self._client: Any = None

    def _ensure_client(self) -> Any:
        from openai import OpenAI

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
        response = _openai_chat_completions_with_retry(client, kwargs)

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
    """Extract *initial contiguous* ``role: system`` entries for Anthropic ``system``.

    Later ``role: system`` messages remain in the returned list; see
    `_normalize_messages_for_anthropic` for how they are mapped to ``role: user``.
    """
    system_parts: list[str] = []
    i = 0
    n = len(msgs)
    while i < n and str(msgs[i].get("role", "")) == "system":
        m = msgs[i]
        content = m.get("content", "")
        if isinstance(content, str):
            system_parts.append(content)
        elif isinstance(content, (dict, list)):
            system_parts.append(json.dumps(content))
        else:
            system_parts.append(str(content))
        i += 1
    rest = list(msgs[i:])
    return ("\n".join(system_parts) if system_parts else None, rest)


def _merge_consecutive_user_turns(
    msgs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse back-to-back ``role: user`` entries into one.

    Anthropic rejects consecutive user turns. This arises when a non-leading
    ``role: system`` message is emitted as a user turn immediately before a
    real ``role: user`` entry (the ``[assistant, system, user]`` ordering).
    Both string and block-list content shapes are handled.
    """
    out: list[dict[str, Any]] = []
    for m in msgs:
        if out and out[-1].get("role") == "user" and m.get("role") == "user":
            prev_content = out[-1].get("content")
            curr_content = m.get("content")
            if isinstance(prev_content, list) and isinstance(curr_content, list):
                merged: Any = prev_content + curr_content
            elif isinstance(prev_content, list):
                merged = prev_content + [{"type": "text", "text": str(curr_content)}]
            elif isinstance(curr_content, list):
                merged = [{"type": "text", "text": str(prev_content)}] + curr_content
            else:
                merged = f"{prev_content}\n\n{curr_content}"
            out[-1] = {"role": "user", "content": merged}
        else:
            out.append(m)
    return out


def _normalize_messages_for_anthropic(
    msgs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert neutral dicts to Anthropic's messages format.

    Consecutive ``role: tool`` entries become one ``role: user`` message whose
    ``content`` lists all ``tool_result`` blocks (Anthropic rejects multiple
    back-to-back user turns with only tool results).

    Anthropic has no in-message ``role: system``. Non-leading system lines are
    folded into the **previous** message when that message is ``role: user`` so
    we never emit two consecutive ``user`` turns (which the API rejects). If
    there is no prior user message, system text is emitted as a standalone user
    turn. A final ``_merge_consecutive_user_turns`` pass handles the residual
    ``[assistant, system, user]`` ordering where the system-as-user turn would
    immediately precede the real user turn.
    """
    out: list[dict[str, Any]] = []
    i = 0
    n = len(msgs)
    while i < n:
        m = msgs[i]
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)

        if role == "tool":
            tool_blocks: list[dict[str, Any]] = []
            while i < n and str(msgs[i].get("role", "")) == "tool":
                tm = msgs[i]
                tc_content = tm.get("content", "")
                if not isinstance(tc_content, str):
                    tc_content = str(tc_content)
                tool_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": str(tm.get("tool_call_id", "")),
                        "content": tc_content,
                    }
                )
                i += 1
            out.append({"role": "user", "content": tool_blocks})
            continue

        if role == "system":
            if out and out[-1].get("role") == "user":
                prev = out[-1]
                prev_content = prev.get("content")
                if isinstance(prev_content, str):
                    out[-1] = {
                        "role": "user",
                        "content": (f"{prev_content}\n\n{_NON_LEADING_SYSTEM_MARK}\n{content}"),
                    }
                elif isinstance(prev_content, list):
                    merged_blocks: list[dict[str, Any]] = [
                        *prev_content,
                        {
                            "type": "text",
                            "text": f"{_NON_LEADING_SYSTEM_MARK}\n{content}",
                        },
                    ]
                    out[-1] = {"role": "user", "content": merged_blocks}
                else:
                    out.append(
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": content}],
                        }
                    )
            else:
                out.append(
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": content}],
                    }
                )
            i += 1
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
                i += 1
                continue

        out.append({"role": role, "content": content})
        i += 1
    return _merge_consecutive_user_turns(out)


class _AnthropicChatAdapter:
    """Direct ``anthropic.Anthropic`` implementation of ``BoundChatModel``."""

    def __init__(self, *, model: str, with_tools: bool) -> None:
        self._model = model
        self._with_tools = with_tools
        self._max_tokens = DEFAULT_MAX_TOKENS
        self._api_key: str = ""
        self._client: Any = None

    def _ensure_client(self) -> Any:
        from anthropic import Anthropic

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

        if not normalized:
            raise ValueError(
                "Anthropic requires at least one non-system message; "
                "invoke was called with an empty messages list after system-prompt extraction. "
                "Ensure the graph state contains at least one user or assistant message before "
                "routing to a node that calls the Anthropic adapter."
            )

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
        response = _anthropic_messages_create_with_retry(client, kwargs)

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
