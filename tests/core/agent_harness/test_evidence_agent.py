"""Regression tests for ``core.agent_harness.agents.evidence_agent.gather_tool_evidence``.

The public contract (docstring) is: *any* failure is reported and swallowed
(returns ``None``) so the conversational turn never breaks. Tool discovery,
integration resolution, and LLM load all run inside ``gather_tool_evidence``'s
single try/except. These tests pin that a raise from any of those paths is
swallowed rather than propagated to the caller.
"""

from __future__ import annotations

from typing import Any

import core.agent_harness.agents.evidence_agent as evidence_agent
import tools.investigation.stages.gather_evidence.tools as gather_tools
from core.agent_harness.session import ReplSession


class _RecordingReporter:
    """Minimal ErrorReporter that records what it was handed."""

    def __init__(self) -> None:
        self.calls: list[tuple[BaseException, str, bool]] = []

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        self.calls.append((exc, context, expected))


def _session() -> ReplSession:
    session = ReplSession()
    session.resolved_integrations_cache = {}
    return session


def test_tool_discovery_raise_is_swallowed(monkeypatch: Any) -> None:
    """A raise from ``get_available_tools`` must not break the turn."""
    monkeypatch.setattr(
        evidence_agent, "_resolve_gather_integrations", lambda _session, _message: {}
    )

    def _boom(_resolved: dict[str, Any]) -> Any:
        raise RuntimeError("tool registry import blew up")

    monkeypatch.setattr(gather_tools, "get_available_tools", _boom)

    reporter = _RecordingReporter()
    result = evidence_agent.gather_tool_evidence(
        "why did it fail?", _session(), error_reporter=reporter
    )

    assert result is None
    assert len(reporter.calls) == 1
    assert isinstance(reporter.calls[0][0], RuntimeError)


def test_integration_resolution_raise_is_swallowed(monkeypatch: Any) -> None:
    """A raise from integration resolution must not break the turn."""

    def _boom(_session: Any, _message: str) -> dict[str, Any]:
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
    """Even without an error reporter, a discovery raise returns None (no crash)."""
    monkeypatch.setattr(
        evidence_agent, "_resolve_gather_integrations", lambda _session, _message: {}
    )

    def _boom(_resolved: dict[str, Any]) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(gather_tools, "get_available_tools", _boom)

    assert evidence_agent.gather_tool_evidence("q", _session()) is None


def test_no_usable_tools_returns_none(monkeypatch: Any) -> None:
    """Empty toolset short-circuits to None without invoking the LLM."""
    monkeypatch.setattr(
        evidence_agent, "_resolve_gather_integrations", lambda _session, _message: {}
    )
    monkeypatch.setattr(gather_tools, "get_available_tools", lambda _resolved: [])

    assert evidence_agent.gather_tool_evidence("q", _session()) is None
