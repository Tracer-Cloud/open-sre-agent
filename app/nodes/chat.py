"""Chat branch nodes - routing, LLM response, and tool execution."""

from __future__ import annotations

import copy
import json
from typing import Any

from app.config import ANTHROPIC_LLM_CONFIG, OPENAI_LLM_CONFIG
from app.constants.prompts import GENERAL_SYSTEM_PROMPT, ROUTER_PROMPT, SYSTEM_PROMPT
from app.services import get_llm_for_tools
from app.services.chat_sdk_adapter import (
    build_bound_chat_model,
    messages_to_invocation_dicts,
)
from app.state import AgentState, ChatMessage
from app.tools.registry import get_registered_tools
from app.types.chat import AssistantTurn, BoundChatModel
from app.types.config import NodeConfig
from app.utils.cfg_helpers import CfgHelpers

# LangChain type -> ChatMessage role mapping (router normalization)
_TYPE_TO_ROLE: dict[str, str] = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
}


def _normalize_messages(msgs: list[Any]) -> list[ChatMessage]:
    """Normalize messages from LangChain format to plain ChatMessage dicts."""
    result: list[ChatMessage] = []
    for m in msgs:
        if hasattr(m, "type") and hasattr(m, "content"):
            role = _TYPE_TO_ROLE.get(m.type, "user")
            result.append({"role": role, "content": str(m.content)})  # type: ignore[typeddict-item]
            continue
        if not isinstance(m, dict):
            continue
        if "role" in m:
            result.append(m)  # type: ignore[arg-type]
            continue
        if "type" in m:
            role = _TYPE_TO_ROLE.get(m["type"], "user")
            result.append({"role": role, "content": str(m.get("content", ""))})  # type: ignore[typeddict-item]
            continue
        result.append(m)  # type: ignore[arg-type]
    return result


# ── Chat LLM ─────────────────────────────────────────────────────────────


_chat_llm_cache: dict[str, BoundChatModel] = {}
_chat_llm_with_tools_cache: dict[str, BoundChatModel] = {}


def reset_chat_llm_cache() -> None:
    """Clear cached chat models after provider/model configuration changes."""
    _chat_llm_cache.clear()
    _chat_llm_with_tools_cache.clear()


class UnsupportedChatProviderError(ValueError):
    """Raised when chat mode is used with an unsupported provider."""


def _resolve_models(provider: str) -> tuple[str, str]:
    """Resolve tool and reasoning model names for the active provider."""
    if provider == "codex":
        raise UnsupportedChatProviderError(
            "Interactive chat requires LLM_PROVIDER=anthropic or openai. "
            "LLM_PROVIDER=codex only supports `opensre investigate` (OpenAI Codex CLI)."
        )
    if provider == "openai":
        return (
            CfgHelpers.first_env_or_default(
                env_keys=(
                    "OPENAI_TOOLCALL_MODEL",
                    "OPENAI_REASONING_MODEL",
                    "OPENAI_MODEL",
                ),
                default=OPENAI_LLM_CONFIG.toolcall_model,
            ),
            CfgHelpers.first_env_or_default(
                env_keys=("OPENAI_REASONING_MODEL", "OPENAI_MODEL"),
                default=OPENAI_LLM_CONFIG.reasoning_model,
            ),
        )
    if provider == "anthropic":
        return (
            CfgHelpers.first_env_or_default(
                env_keys=(
                    "ANTHROPIC_TOOLCALL_MODEL",
                    "ANTHROPIC_REASONING_MODEL",
                    "ANTHROPIC_MODEL",
                ),
                default=ANTHROPIC_LLM_CONFIG.toolcall_model,
            ),
            CfgHelpers.first_env_or_default(
                env_keys=("ANTHROPIC_REASONING_MODEL", "ANTHROPIC_MODEL"),
                default=ANTHROPIC_LLM_CONFIG.reasoning_model,
            ),
        )
    raise ValueError(f"Unsupported chat model provider: {provider}")


def _get_chat_llm(*, with_tools: bool = False) -> BoundChatModel:
    """Get the provider-aware chat model used by chat nodes."""
    provider = CfgHelpers.resolve_llm_provider()
    tool_model, reasoning_model = _resolve_models(provider)

    if with_tools:
        cache_key = f"{provider}:{tool_model}:tools"
        cached_tool_model = _chat_llm_with_tools_cache.get(cache_key)
        if cached_tool_model is None:
            cached_tool_model = build_bound_chat_model(
                provider=provider,
                model_name=tool_model,
                with_tools=True,
            )
            _chat_llm_with_tools_cache[cache_key] = cached_tool_model
        return cached_tool_model

    cache_key = f"{provider}:{reasoning_model}"
    cached_reasoning_model = _chat_llm_cache.get(cache_key)
    if cached_reasoning_model is None:
        cached_reasoning_model = build_bound_chat_model(
            provider=provider,
            model_name=reasoning_model,
            with_tools=False,
        )
        _chat_llm_cache[cache_key] = cached_reasoning_model
    return cached_reasoning_model


def _assistant_turn_to_state_message(turn: AssistantTurn) -> dict[str, Any]:
    """Persist one model turn in the graph state as a plain message dict."""
    msg: dict[str, Any] = {"role": "assistant", "content": turn.get("content", "")}
    tcs = turn.get("tool_calls")
    if tcs:
        msg["tool_calls"] = list(tcs)
    return msg


def _has_system_message(msgs: list[dict[str, Any]]) -> bool:
    return any(m.get("role") == "system" for m in msgs)


def _prepare_chat_invoke_messages(
    raw: list[Any],
    *,
    default_system: str,
) -> list[Any]:
    """Normalize graph messages, ensure a system prompt, apply guardrails once."""
    msgs = messages_to_invocation_dicts(raw)
    if not _has_system_message(msgs):
        msgs = [{"role": "system", "content": default_system}, *msgs]
    return _apply_guardrails_to_messages(msgs)


def _apply_guardrails_to_messages(msgs: list[Any]) -> list[Any]:
    """Return a copy of *msgs* with redacted content, leaving originals untouched.

    Supports neutral dict messages and legacy objects with a ``content`` attribute
    (used by guardrail tests and any pre-migration graph state).
    """
    from app.guardrails.engine import get_guardrail_engine

    engine = get_guardrail_engine()
    if not engine.is_active:
        return msgs
    result: list[Any] = []
    for msg in msgs:
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str) and content:
                redacted = engine.apply(content)
                if redacted != content:
                    msg = dict(msg)
                    msg["content"] = redacted
            result.append(msg)
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content:
            redacted = engine.apply(content)
            if redacted != content:
                msg = copy.copy(msg)
                msg.content = redacted
        result.append(msg)
    return result


# ── Node functions ───────────────────────────────────────────────────────


def router_node(state: AgentState) -> dict[str, Any]:
    """Route chat messages by intent."""
    msgs = _normalize_messages(list(state.get("messages", [])))
    if not msgs or msgs[-1].get("role") != "user":
        return {"route": "general"}

    response = get_llm_for_tools().invoke(
        [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": str(msgs[-1].get("content", ""))},
        ]
    )
    route = str(response.content).strip().lower()
    return {"route": route if route in ("tracer_data", "general") else "general"}


def chat_agent_node(state: AgentState, _config: NodeConfig | None = None) -> dict[str, Any]:
    """Chat agent with tools for Tracer data queries.

    Uses the configured provider with bound tools. The LLM can make tool calls
    which will be executed by the tool_executor node.
    """
    raw = list(state.get("messages", []))
    msgs = _prepare_chat_invoke_messages(raw, default_system=SYSTEM_PROMPT)
    try:
        llm = _get_chat_llm(with_tools=True)
    except UnsupportedChatProviderError as exc:
        return {"messages": [_assistant_turn_to_state_message({"content": str(exc)})]}
    turn = llm.invoke(msgs)
    return {"messages": [_assistant_turn_to_state_message(turn)]}


def general_node(state: AgentState, _config: NodeConfig | None = None) -> dict[str, Any]:
    """Direct LLM response without tools for general questions."""
    raw = list(state.get("messages", []))
    msgs = _prepare_chat_invoke_messages(raw, default_system=GENERAL_SYSTEM_PROMPT)
    try:
        llm = _get_chat_llm(with_tools=False)
    except UnsupportedChatProviderError as exc:
        return {"messages": [_assistant_turn_to_state_message({"content": str(exc)})]}
    turn = llm.invoke(msgs)
    return {"messages": [_assistant_turn_to_state_message(turn)]}


def tool_executor_node(state: AgentState) -> dict[str, Any]:
    """Execute tool calls from the last AI message and return tool result dicts."""
    msgs = messages_to_invocation_dicts(list(state.get("messages", [])))
    if not msgs:
        return {"messages": []}

    last_ai: dict[str, Any] | None = None
    for m in reversed(msgs):
        if m.get("role") != "assistant":
            continue
        tcs = m.get("tool_calls")
        if tcs:
            last_ai = m
            break

    if not last_ai:
        return {"messages": []}

    tool_calls = last_ai.get("tool_calls") or []

    tool_map = {t.name: t for t in get_registered_tools("chat")}

    tool_messages: list[dict[str, Any]] = []
    for tc in tool_calls:
        tool_name = str(tc.get("name", ""))
        tool_args = tc.get("args", {})
        if not isinstance(tool_args, dict):
            tool_args = {}
        tool_id = str(tc.get("id", ""))

        try:
            reg = tool_map.get(tool_name)
            if reg is None:
                result = json.dumps({"error": f"Unknown tool: {tool_name}"})
            else:
                out = reg(**tool_args)
                result = out if isinstance(out, str) else json.dumps(out, default=str)
        # Catch recoverable tool failures broadly (SDK / IO / import / JSON, etc.).
        # BaseException is not caught so SystemExit / KeyboardInterrupt still propagate.
        except Exception as e:
            result = json.dumps({"error": str(e)})

        tool_messages.append(
            {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_id,
                "name": tool_name,
            }
        )

    return {"messages": tool_messages}
