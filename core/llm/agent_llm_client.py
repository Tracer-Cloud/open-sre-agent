"""Investigation agent's tool-calling LLM client (thin wrapper over the factory).

Provider routing lives in :mod:`core.llm.factory`; this module keeps the historical
``get_agent_llm`` name and re-exports the tool-calling client classes callers use.
"""

from __future__ import annotations

from core.llm.factory import LLMRole, get_llm, reset_llm_clients
from core.llm.shared.tool_schema_normalize import build_openai_tool_specs
from core.llm.transports.sdk.agent_clients import (
    AnthropicAgentClient,
    BedrockAgentClient,
    BedrockConverseAgentClient,
    CLIBackedAgentClient,
    OpenAIAgentClient,
    _try_parse_tool_call_json,
)
from core.llm.types import AgentLLMClient

__all__ = [
    "AnthropicAgentClient",
    "BedrockAgentClient",
    "BedrockConverseAgentClient",
    "CLIBackedAgentClient",
    "OpenAIAgentClient",
    "_try_parse_tool_call_json",
    "build_openai_tool_specs",
    "get_agent_llm",
    "reset_agent_client",
]


def get_llm(LLMRole.AGENT) -> AgentLLMClient:
    """Return the singleton tool-calling LLM client for the investigation agent."""
    return get_llm(LLMRole.AGENT)


def reset_agent_client() -> None:
    """Reset the cached LLM clients (for tests / config changes)."""
    reset_llm_clients()
