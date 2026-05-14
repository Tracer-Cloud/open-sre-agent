"""Tool-calling LLM client for the investigation agent ReAct loop.

Supports Anthropic and OpenAI (and OpenAI-compatible providers).
The investigation agent sends all tool schemas upfront; the LLM decides
which to call. This module handles the provider-specific message formats.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_RETRY_INITIAL_BACKOFF_SEC = 1.0
_RETRY_MAX_ATTEMPTS = 3
_CLIENT_TIMEOUT_SEC = 90.0


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AgentLLMResponse:
    """Response from the agent LLM — may include text and/or tool calls."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    # Raw Anthropic content blocks — used to build the next assistant message
    # for providers that require full content-block history (Anthropic).
    raw_content: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _anthropic_tool_schema(tool: Any) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def _openai_tool_schema(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


class AnthropicAgentClient:
    """Anthropic client with native tool-calling for the agent loop."""

    def __init__(self, model: str, max_tokens: int = 4096) -> None:
        from anthropic import Anthropic

        from app.llm_credentials import resolve_llm_api_key

        api_key = resolve_llm_api_key("ANTHROPIC_API_KEY")
        self._client = Anthropic(api_key=api_key, timeout=_CLIENT_TIMEOUT_SEC)
        self._model = model
        self._max_tokens = max_tokens

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [_anthropic_tool_schema(t) for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        from anthropic import AuthenticationError, BadRequestError, NotFoundError

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        backoff = _RETRY_INITIAL_BACKOFF_SEC
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                response = self._client.messages.create(**kwargs)
                break
            except AuthenticationError as err:
                raise RuntimeError(
                    "Anthropic authentication failed. Check ANTHROPIC_API_KEY."
                ) from err
            except NotFoundError as err:
                raise RuntimeError(f"Anthropic model '{self._model}' not found.") from err
            except BadRequestError as err:
                raise RuntimeError(f"Anthropic request rejected (HTTP 400): {err.message}") from err
            except Exception as err:
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"Anthropic API failed after {_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError("Anthropic invocation failed") from last_err

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))

        return AgentLLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=str(response.stop_reason),
            raw_content=response.content,
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        """Build the Anthropic tool_result user message for one round of tool calls."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(result, default=str),
                }
                for tc, result in zip(tool_calls, results)
            ],
        }

    @staticmethod
    def build_assistant_message(raw_content: Any) -> dict[str, Any]:
        """Build the assistant message preserving full Anthropic content blocks."""
        return {"role": "assistant", "content": raw_content}


class OpenAIAgentClient:
    """OpenAI-compatible client with tool-calling for the agent loop."""

    def __init__(
        self,
        model: str,
        max_tokens: int = 4096,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        api_key_default: str = "",
    ) -> None:
        from openai import OpenAI

        from app.llm_credentials import resolve_llm_api_key

        api_key = resolve_llm_api_key(api_key_env) or api_key_default
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=_CLIENT_TIMEOUT_SEC)
        self._model = model
        self._max_tokens = max_tokens

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [_openai_tool_schema(t) for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        from openai import AuthenticationError, BadRequestError, NotFoundError

        msgs = list(messages)
        if system:
            msgs = [{"role": "system", "content": system}] + msgs

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": msgs,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        backoff = _RETRY_INITIAL_BACKOFF_SEC
        last_err: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                response = self._client.chat.completions.create(**kwargs)
                break
            except AuthenticationError as err:
                raise RuntimeError("OpenAI authentication failed.") from err
            except NotFoundError as err:
                raise RuntimeError(f"OpenAI model '{self._model}' not found.") from err
            except BadRequestError as err:
                raise RuntimeError(f"OpenAI request rejected: {err}") from err
            except Exception as err:
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(f"OpenAI API failed: {err}") from err
                time.sleep(backoff)
                backoff *= 2
        else:
            raise RuntimeError("OpenAI invocation failed") from last_err

        choice = response.choices[0]
        msg = choice.message
        content = msg.content or ""
        stop_reason = choice.finish_reason or "stop"

        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    input_dict = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    input_dict = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=input_dict))

        return AgentLLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw_content=None,
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        raise NotImplementedError("OpenAI tool results must be appended as separate messages")

    @staticmethod
    def build_tool_result_messages(
        tool_calls: list[ToolCall], results: list[Any]
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            }
            for tc, result in zip(tool_calls, results)
        ]

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                }
                for tc in tool_calls
            ]
        return msg


_AgentClientType = AnthropicAgentClient | OpenAIAgentClient
_agent_client: _AgentClientType | None = None


def get_agent_llm() -> _AgentClientType:
    """Return a singleton tool-calling LLM client for the investigation agent."""
    global _agent_client
    if _agent_client is not None:
        return _agent_client

    from pydantic import ValidationError

    from app.config import LLMSettings

    try:
        settings = LLMSettings.from_env()
    except ValidationError as exc:
        raise RuntimeError(str(exc)) from exc

    provider = settings.provider
    if provider == "openai":
        from app.config import OPENAI_LLM_CONFIG

        _agent_client = OpenAIAgentClient(
            model=settings.openai_reasoning_model,
            max_tokens=OPENAI_LLM_CONFIG.max_tokens,
        )
    elif provider in ("openrouter", "gemini", "nvidia", "minimax", "requesty", "ollama"):
        # All OpenAI-compatible providers
        from app.config import LLMSettings

        _agent_client = _create_openai_compat_client(settings, provider)
    else:
        # Default: Anthropic
        from app.config import ANTHROPIC_LLM_CONFIG

        _agent_client = AnthropicAgentClient(
            model=settings.anthropic_reasoning_model,
            max_tokens=ANTHROPIC_LLM_CONFIG.max_tokens,
        )

    return _agent_client


def _create_openai_compat_client(settings: Any, provider: str) -> OpenAIAgentClient:
    from app.config import (
        GEMINI_BASE_URL,
        MINIMAX_BASE_URL,
        NVIDIA_BASE_URL,
        OPENROUTER_BASE_URL,
    )

    provider_map: dict[str, tuple[str, str, str]] = {
        "openrouter": (
            OPENROUTER_BASE_URL,
            "OPENROUTER_API_KEY",
            settings.openrouter_reasoning_model,
        ),
        "gemini": (GEMINI_BASE_URL, "GEMINI_API_KEY", settings.gemini_reasoning_model),
        "nvidia": (NVIDIA_BASE_URL, "NVIDIA_API_KEY", settings.nvidia_reasoning_model),
        "minimax": (MINIMAX_BASE_URL, "MINIMAX_API_KEY", settings.minimax_reasoning_model),
        "requesty": (
            "https://router.requesty.ai/v1",
            "REQUESTY_API_KEY",
            settings.requesty_reasoning_model,
        ),
    }
    if provider == "ollama":
        host = settings.ollama_host.rstrip("/")
        return OpenAIAgentClient(
            model=settings.ollama_model,
            max_tokens=1024,
            base_url=f"{host}/v1",
            api_key_env="OLLAMA_API_KEY",
            api_key_default="ollama",
        )
    base_url, api_key_env, model = provider_map[provider]
    return OpenAIAgentClient(model=model, base_url=base_url, api_key_env=api_key_env)


def reset_agent_client() -> None:
    """Reset the singleton (for tests / config changes)."""
    global _agent_client
    _agent_client = None
