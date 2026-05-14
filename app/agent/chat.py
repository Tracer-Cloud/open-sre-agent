"""Chat agent — routes messages, invokes LLM with tools, returns responses."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from app.constants.prompts import GENERAL_SYSTEM_PROMPT, ROUTER_PROMPT, SYSTEM_PROMPT
from app.guardrails.engine import GuardrailBlockedError
from app.services import get_llm_for_tools
from app.services.chat_sdk_adapter import (
    _coerce_text_field,
    build_bound_chat_model,
    messages_to_invocation_dicts,
)
from app.state import AgentState
from app.tools.registry import get_registered_tools
from app.types.config import NodeConfig
from app.utils.cfg_helpers import CfgHelpers

logger = logging.getLogger(__name__)

_MAX_CHAT_TOOL_ROUNDS = 6


class UnsupportedChatProviderError(ValueError):
    pass


class ChatAgent:
    """Chat loop: route → LLM → optional tool execution → response."""

    def run(
        self,
        state: AgentState,
        _config: NodeConfig | None = None,
    ) -> dict[str, Any]:
        """Process a chat turn. Returns state updates (messages list)."""
        route = _route(state)
        if route == "tracer_data":
            return _chat_with_tools(state)
        return _chat_general(state)

    def invoke(self, state: AgentState, _config: NodeConfig | None = None) -> dict[str, Any]:
        return self.run(state, _config)


def _route(state: AgentState) -> str:
    msgs = messages_to_invocation_dicts(list(state.get("messages", [])))
    if not msgs or msgs[-1].get("role") != "user":
        return "general"
    try:
        response = get_llm_for_tools().invoke(
            [
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": _coerce_text_field(msgs[-1].get("content"))},
            ]
        )
        route = str(response.content).strip().lower()
        return route if route in ("tracer_data", "general") else "general"
    except Exception as err:
        logger.warning("Router failed, defaulting to general: %s", err)
        return "general"


def _chat_with_tools(state: AgentState) -> dict[str, Any]:
    raw = list(state.get("messages", []))
    msgs = _prepare_messages(raw, SYSTEM_PROMPT)
    try:
        llm = _get_llm(with_tools=True)
    except UnsupportedChatProviderError as exc:
        return {"messages": [{"role": "assistant", "content": str(exc)}]}

    new_messages: list[dict[str, Any]] = []
    for _ in range(_MAX_CHAT_TOOL_ROUNDS):
        turn = llm.invoke(msgs)
        assistant_msg = _turn_to_message(turn)
        new_messages.append(assistant_msg)
        msgs.append(assistant_msg)

        if not assistant_msg.get("tool_calls"):
            break

        tool_state = cast(AgentState, {"messages": msgs})
        tool_messages = execute_tool_calls(tool_state).get("messages", [])
        if not tool_messages:
            break
        new_messages.extend(tool_messages)
        msgs.extend(tool_messages)
    else:
        new_messages.append(
            {
                "role": "assistant",
                "content": "I hit the chat tool-call limit before producing a final answer.",
            }
        )

    return {"messages": new_messages}


def _chat_general(state: AgentState) -> dict[str, Any]:
    raw = list(state.get("messages", []))
    msgs = _prepare_messages(raw, GENERAL_SYSTEM_PROMPT)
    try:
        llm = _get_llm(with_tools=False)
    except UnsupportedChatProviderError as exc:
        return {"messages": [{"role": "assistant", "content": str(exc)}]}
    turn = llm.invoke(msgs)
    return {"messages": [_turn_to_message(turn)]}


def execute_tool_calls(state: AgentState) -> dict[str, Any]:
    """Execute pending tool calls from the last assistant message."""
    msgs = messages_to_invocation_dicts(list(state.get("messages", [])))
    if not msgs:
        return {"messages": []}

    last_ai = next(
        (m for m in reversed(msgs) if m.get("role") == "assistant" and m.get("tool_calls")),
        None,
    )
    if not last_ai:
        return {"messages": []}

    tool_map = {t.name: t for t in get_registered_tools("chat")}
    tool_messages: list[dict[str, Any]] = []

    for tc in last_ai.get("tool_calls") or []:
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
        except GuardrailBlockedError:
            raise
        except Exception as exc:
            result = json.dumps({"error": str(exc)})

        tool_messages.append(
            {"role": "tool", "content": result, "tool_call_id": tool_id, "name": tool_name}
        )

    return {"messages": tool_messages}


_llm_cache: dict[str, Any] = {}


def _get_llm(*, with_tools: bool) -> Any:
    from app.config import ANTHROPIC_LLM_CONFIG, OPENAI_LLM_CONFIG

    provider = CfgHelpers.resolve_llm_provider()
    if provider == "codex":
        raise UnsupportedChatProviderError(
            "Interactive chat requires LLM_PROVIDER=anthropic or openai."
        )
    if provider == "openai":
        model = CfgHelpers.first_env_or_default(
            env_keys=("OPENAI_TOOLCALL_MODEL", "OPENAI_REASONING_MODEL", "OPENAI_MODEL"),
            default=OPENAI_LLM_CONFIG.toolcall_model
            if with_tools
            else OPENAI_LLM_CONFIG.reasoning_model,
        )
    elif provider == "anthropic":
        model = CfgHelpers.first_env_or_default(
            env_keys=("ANTHROPIC_TOOLCALL_MODEL", "ANTHROPIC_REASONING_MODEL", "ANTHROPIC_MODEL"),
            default=ANTHROPIC_LLM_CONFIG.toolcall_model
            if with_tools
            else ANTHROPIC_LLM_CONFIG.reasoning_model,
        )
    else:
        raise ValueError(f"Unsupported chat model provider: {provider}")

    cache_key = f"{provider}:{model}:{'tools' if with_tools else 'plain'}"
    if cache_key not in _llm_cache:
        _llm_cache[cache_key] = build_bound_chat_model(
            provider=provider, model_name=model, with_tools=with_tools
        )
    return _llm_cache[cache_key]


def reset_chat_cache() -> None:
    _llm_cache.clear()


def _prepare_messages(raw: list[Any], default_system: str) -> list[dict[str, Any]]:
    msgs = messages_to_invocation_dicts(raw)
    if not any(m.get("role") == "system" for m in msgs):
        msgs = [{"role": "system", "content": default_system}, *msgs]
    return _apply_guardrails(msgs)


def _apply_guardrails(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.guardrails.engine import get_guardrail_engine

    engine = get_guardrail_engine()
    if not engine.is_active:
        return msgs
    result: list[dict[str, Any]] = []
    for msg in msgs:
        content = msg.get("content")
        if isinstance(content, str) and content:
            redacted = engine.apply(content)
            if redacted != content:
                msg = dict(msg)
                msg["content"] = redacted
        result.append(msg)
    return result


def _turn_to_message(turn: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": turn.get("content", "")}
    tcs = turn.get("tool_calls")
    if tcs:
        msg["tool_calls"] = list(tcs)
    return msg
