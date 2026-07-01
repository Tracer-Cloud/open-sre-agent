"""Equivalence tests for the ``EvidenceAgent`` -> ``SurfaceHooks`` bridge.

Builds an ``EvidenceAgent`` and asserts the projected hooks return exactly
what the subclass methods return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.agent_harness.evidence_agent import EvidenceAgent
from core.agent_harness.evidence_hooks import evidence_agent_to_hooks
from core.agent_harness.surface_hooks import SurfaceHooks


class _StubSessionStore:
    def __init__(self) -> None:
        self.cli_agent_messages: list[tuple[str, str]] = []
        self.configured_integrations_known: bool = True
        self.configured_integrations: tuple[str, ...] = ()
        self.last_state: dict[str, Any] | None = None
        self.last_synthetic_observation_path: str | None = None
        self.reasoning_effort: Any | None = None
        self.history: list[dict[str, Any]] = []
        self.last_command_observation: str | None = None
        self.session_id: str = "test-session"
        self.resolved_integrations_cache: dict[str, Any] | None = None
        self.github_repo_scope: tuple[str, str] | None = None


@dataclass
class _FakeTool:
    name: str
    source: str = "grafana"

    def __repr__(self) -> str:
        return f"_FakeTool({self.name!r})"


def _make_evidence_agent(message: str = "why did it fail?") -> EvidenceAgent:
    return EvidenceAgent(session=_StubSessionStore(), message=message)  # type: ignore[arg-type]


# ---- Bundle shape ----


def test_bundle_satisfies_the_surface_hooks_protocol() -> None:
    agent = _make_evidence_agent()
    hooks = evidence_agent_to_hooks(agent)
    assert isinstance(hooks, SurfaceHooks)


# ---- resolve_tools ----


def test_resolve_tools_returns_what_build_tools_returns(monkeypatch: Any) -> None:
    """The hook delegates to ``EvidenceAgent.build_tools``."""
    agent = _make_evidence_agent()

    fake_tools = [_FakeTool("grafana_query"), _FakeTool("datadog_logs")]

    def _fake_build_tools() -> list[Any]:
        return list(fake_tools)

    monkeypatch.setattr(agent, "build_tools", _fake_build_tools)

    hooks = evidence_agent_to_hooks(agent)
    projected = hooks.resolve_tools(None)  # type: ignore[arg-type]

    assert [t.name for t in projected] == ["grafana_query", "datadog_logs"]


def test_resolve_tools_returns_a_fresh_list_on_each_call(monkeypatch: Any) -> None:
    """Callers mutating the returned list must not affect the agent."""
    agent = _make_evidence_agent()

    def _fake_build_tools() -> list[Any]:
        return [_FakeTool("static_tool")]

    monkeypatch.setattr(agent, "build_tools", _fake_build_tools)

    hooks = evidence_agent_to_hooks(agent)
    first = hooks.resolve_tools(None)  # type: ignore[arg-type]
    second = hooks.resolve_tools(None)  # type: ignore[arg-type]

    assert first is not second


# ---- construct_prompt ----


def test_construct_prompt_delegates_to_build_system_prompt(monkeypatch: Any) -> None:
    agent = _make_evidence_agent(message="why did it fail?")
    monkeypatch.setattr(agent, "build_system_prompt", lambda: "PROMPT")

    hooks = evidence_agent_to_hooks(agent)

    assert hooks.construct_prompt(None) == "PROMPT"  # type: ignore[arg-type]


def test_construct_prompt_stays_in_sync_with_live_agent_state(monkeypatch: Any) -> None:
    """Repeated calls consult ``EvidenceAgent`` each time (no cached freeze)."""
    agent = _make_evidence_agent()

    prompts = iter(["first", "second"])
    monkeypatch.setattr(agent, "build_system_prompt", lambda: next(prompts))

    hooks = evidence_agent_to_hooks(agent)
    assert hooks.construct_prompt(None) == "first"  # type: ignore[arg-type]
    assert hooks.construct_prompt(None) == "second"  # type: ignore[arg-type]


# ---- No-op hooks ----


def test_inject_context_is_the_identity_projection() -> None:
    agent = _make_evidence_agent()
    hooks = evidence_agent_to_hooks(agent)

    request = {"messages": [], "system": "s", "tools": [], "metadata": {"iteration": 0}}
    assert hooks.inject_context(None, request) is request  # type: ignore[arg-type]


def test_route_response_is_a_noop() -> None:
    agent = _make_evidence_agent()
    hooks = evidence_agent_to_hooks(agent)
    assert hooks.route_response(None, "any text") is None  # type: ignore[arg-type]


# ---- Field prevents unused-import lint ----

_ = field  # keep dataclasses import scope symmetric with sibling test files
