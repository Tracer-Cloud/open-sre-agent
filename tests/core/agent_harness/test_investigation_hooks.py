"""Equivalence tests for the ``ConnectedInvestigationAgent`` -> ``SurfaceHooks`` bridge."""

from __future__ import annotations

from typing import Any

from core.agent_harness.investigation_hooks import investigation_agent_to_hooks
from core.agent_harness.surface_hooks import SurfaceHooks
from tools.investigation.stages.gather_evidence.agent import ConnectedInvestigationAgent


def _make_investigation_agent() -> ConnectedInvestigationAgent:
    return ConnectedInvestigationAgent()


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"_FakeTool({self.name!r})"


# ---- Bundle shape ----


def test_bundle_satisfies_the_surface_hooks_protocol() -> None:
    agent = _make_investigation_agent()
    hooks = investigation_agent_to_hooks(agent)
    assert isinstance(hooks, SurfaceHooks)


# ---- resolve_tools ----


def test_resolve_tools_returns_what_build_tools_returns(monkeypatch: Any) -> None:
    """The hook delegates to ``ConnectedInvestigationAgent.build_tools``."""
    agent = _make_investigation_agent()

    fake_tools = [_FakeTool("query_grafana_logs"), _FakeTool("query_datadog_metrics")]
    monkeypatch.setattr(agent, "build_tools", lambda: list(fake_tools))

    hooks = investigation_agent_to_hooks(agent)
    projected = hooks.resolve_tools(None)  # type: ignore[arg-type]

    assert [t.name for t in projected] == ["query_grafana_logs", "query_datadog_metrics"]


def test_resolve_tools_returns_a_fresh_list_on_each_call(monkeypatch: Any) -> None:
    """Callers mutating the returned list must not affect the agent."""
    agent = _make_investigation_agent()
    monkeypatch.setattr(agent, "build_tools", lambda: [_FakeTool("static_tool")])

    hooks = investigation_agent_to_hooks(agent)
    first = hooks.resolve_tools(None)  # type: ignore[arg-type]
    second = hooks.resolve_tools(None)  # type: ignore[arg-type]

    assert first is not second


# ---- construct_prompt ----


def test_construct_prompt_delegates_to_build_system_prompt(monkeypatch: Any) -> None:
    agent = _make_investigation_agent()
    monkeypatch.setattr(agent, "build_system_prompt", lambda: "INVESTIGATION_PROMPT")

    hooks = investigation_agent_to_hooks(agent)

    assert hooks.construct_prompt(None) == "INVESTIGATION_PROMPT"  # type: ignore[arg-type]


def test_construct_prompt_stays_in_sync_with_live_agent_state(monkeypatch: Any) -> None:
    """Repeated calls consult the agent each time (no cached freeze)."""
    agent = _make_investigation_agent()

    prompts = iter(["stage_1_prompt", "stage_2_prompt"])
    monkeypatch.setattr(agent, "build_system_prompt", lambda: next(prompts))

    hooks = investigation_agent_to_hooks(agent)
    assert hooks.construct_prompt(None) == "stage_1_prompt"  # type: ignore[arg-type]
    assert hooks.construct_prompt(None) == "stage_2_prompt"  # type: ignore[arg-type]


# ---- No-op hooks ----


def test_inject_context_is_the_identity_projection() -> None:
    agent = _make_investigation_agent()
    hooks = investigation_agent_to_hooks(agent)

    request = {"messages": [], "system": "s", "tools": [], "metadata": {"iteration": 0}}
    assert hooks.inject_context(None, request) is request  # type: ignore[arg-type]


def test_route_response_is_a_noop() -> None:
    agent = _make_investigation_agent()
    hooks = investigation_agent_to_hooks(agent)
    assert hooks.route_response(None, "assistant reply") is None  # type: ignore[arg-type]
