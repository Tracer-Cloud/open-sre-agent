"""Model identity for context sizing and analytics must read the public ``model_id``.

The litellm transport client (``OPENSRE_LLM_TRANSPORT=litellm``) exposes its model
name only through the public ``model_id`` property; it has no private ``_model``
attribute. Two call sites used to read ``_model`` directly, so every litellm-routed
turn silently fell back to the conservative default context window and reported an
``unknown`` model to analytics. Both sites now prefer ``model_id`` and keep the
private ``_model`` as a fallback, matching the resolution already used elsewhere in
``react_loop``.
"""

from __future__ import annotations

from typing import Any

from core.agent import Agent
from core.agent.react_loop import ReactLoop
from core.agent.run_io import AgentRunInput
from core.agent_harness.accounting.token_accounting import resolve_model_name
from core.context_budget import context_budget_ceiling_for_model

_CLAUDE_MODEL = "anthropic/claude-sonnet-4-5"
_CLAUDE_CEILING = context_budget_ceiling_for_model(_CLAUDE_MODEL)
_DEFAULT_CEILING = context_budget_ceiling_for_model(None)


class _LiteLLMStyleClient:
    """Fake litellm transport client: public ``model_id`` only, no ``_model``."""

    def __init__(self, model: str) -> None:
        self._litellm_model = model

    @property
    def model_id(self) -> str | None:
        return self._litellm_model

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []


class _DirectStyleClient:
    """Fake SDK transport client that exposes only the private ``_model``."""

    def __init__(self, model: str) -> None:
        self._model = model

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []


def _build_loop(client: Any) -> ReactLoop[Any]:
    agent: Agent[Any] = Agent(llm=client, system="sys", tools=[], max_iterations=1)
    run_input = AgentRunInput[Any].from_messages(
        [{"role": "user", "content": "hi"}],
        llm=client,
        system="sys",
        tools=[],
        resolved=None,
        tool_resources={},
        max_iterations=1,
    )
    return ReactLoop(run_input, agent)


def test_react_loop_sizes_context_from_litellm_model_id() -> None:
    loop = _build_loop(_LiteLLMStyleClient(_CLAUDE_MODEL))
    assert loop._ceiling == _CLAUDE_CEILING
    assert loop._ceiling != _DEFAULT_CEILING


def test_react_loop_still_sizes_context_from_direct_model() -> None:
    loop = _build_loop(_DirectStyleClient(_CLAUDE_MODEL))
    assert loop._ceiling == _CLAUDE_CEILING


def test_resolve_model_name_reads_litellm_model_id() -> None:
    assert resolve_model_name(_LiteLLMStyleClient(_CLAUDE_MODEL)) == _CLAUDE_MODEL


def test_resolve_model_name_falls_back_to_direct_model() -> None:
    assert resolve_model_name(_DirectStyleClient(_CLAUDE_MODEL)) == _CLAUDE_MODEL
