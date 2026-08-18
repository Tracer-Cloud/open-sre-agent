"""Regression tests for ``core.agent_harness.turns.evidence_driver.gather_tool_evidence``."""

from __future__ import annotations

from typing import Any

import core.agent_harness.turns.evidence_driver as evidence_agent
import platform.harness_ports as harness_ports
from surfaces.interactive_shell.session import Session


class _RecordingReporter:
    def __init__(self) -> None:
        self.calls: list[tuple[BaseException, str, bool]] = []

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        self.calls.append((exc, context, expected))


def _session() -> Session:
    session = Session()
    session.resolved_integrations_cache = {}
    return session


def test_tool_discovery_raise_is_swallowed(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        evidence_agent,
        "_resolve_gather_integrations",
        lambda *_args, **_kwargs: {},
    )

    def _boom(_resolved: dict[str, Any]) -> Any:
        raise RuntimeError("tool registry import blew up")

    monkeypatch.setattr(harness_ports, "get_investigation_tools", _boom)

    reporter = _RecordingReporter()
    result = evidence_agent.gather_tool_evidence(
        "why did it fail?", _session(), error_reporter=reporter
    )

    assert result is None
    assert len(reporter.calls) == 1
    assert isinstance(reporter.calls[0][0], RuntimeError)


def test_integration_resolution_raise_is_swallowed(monkeypatch: Any) -> None:
    def _boom(_session: Any, _message: str, resolved_integrations: Any = None) -> dict[str, Any]:
        raise RuntimeError("credential store unreadable")

    monkeypatch.setattr(evidence_agent, "_resolve_gather_integrations", _boom)

    reporter = _RecordingReporter()
    result = evidence_agent.gather_tool_evidence(
        "any open issues?", _session(), error_reporter=reporter
    )

    assert result is None
    assert len(reporter.calls) == 1
    assert isinstance(reporter.calls[0][0], RuntimeError)


def test_no_error_reporter_still_swallows(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        evidence_agent,
        "_resolve_gather_integrations",
        lambda *_args, **_kwargs: {},
    )

    def _boom(_resolved: dict[str, Any]) -> Any:
        raise RuntimeError("tool registry import blew up")

    monkeypatch.setattr(harness_ports, "get_investigation_tools", _boom)

    assert evidence_agent.gather_tool_evidence("why?", _session()) is None


def test_empty_tools_returns_none(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        evidence_agent,
        "_resolve_gather_integrations",
        lambda *_args, **_kwargs: {"datadog": {}},
    )
    monkeypatch.setattr(harness_ports, "get_investigation_tools", lambda _resolved: [])

    assert evidence_agent.gather_tool_evidence("status?", _session()) is None


class _FakeGatherLLM:
    model_id: str | None = None

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:  # noqa: ARG002
        return []

    def invoke(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("not invoked in this test")


def test_default_gather_agent_gets_a_goal_reviewer_over_the_question() -> None:
    """A gather turn that concludes on discovery-only output must be nudged.

    The default factory wires a gather-flavored reviewed goal over the user's
    question, so the loop cannot stop after e.g. an MCP tool listing without
    one goal review pass.
    """
    agent = evidence_agent._build_evidence_agent(
        llm=_FakeGatherLLM(),
        session=_session(),
        gather_tools=[],
        resolved={},
        on_progress=None,
        message="how many posthog users do we have on windows?",
    )

    assert agent._goal is not None
    assert "how many posthog users do we have on windows?" in agent._goal.description
    assert agent._goal.verify is not None
