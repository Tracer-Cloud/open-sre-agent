"""Tests for the interactive-shell tool-gathering pass.

``gather_tool_evidence`` runs a bounded tool-calling loop over the same
registered tools the investigation uses and returns the collected outputs as a
formatted observation block (or ``None`` when there is nothing to add). These
tests exercise the no-tools, executed-results, no-executed, and exception paths
without any live LLM by monkeypatching the lazily-imported collaborators.
"""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

import app.agent.stages.investigate.tools as investigate_tools
import app.agent.tool_loop as tool_loop
import app.services.agent_llm_client as agent_llm_client
from app.cli.interactive_shell.chat.tool_gathering import gather_tool_evidence
from app.cli.interactive_shell.runtime.session import ReplSession


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=80)


class _DummyTool:
    def __init__(self, name: str, source: str = "github") -> None:
        self.name = name
        self.source = source


def test_no_tools_available_returns_none(monkeypatch: Any) -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(investigate_tools, "get_available_tools", lambda _resolved: [])

    assert gather_tool_evidence("any question", session, _console()) is None


def test_secondary_only_tools_return_none(monkeypatch: Any) -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(
        investigate_tools,
        "get_available_tools",
        lambda _resolved: [_DummyTool("get_sre_guidance", source="knowledge")],
    )

    def _unexpected_llm() -> Any:
        raise AssertionError("knowledge-only tools should not invoke the gather loop")

    monkeypatch.setattr(agent_llm_client, "get_agent_llm", _unexpected_llm)

    assert gather_tool_evidence("why did it fail?", session, _console()) is None


def test_executed_results_return_formatted_observation(monkeypatch: Any) -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(
        investigate_tools,
        "get_available_tools",
        lambda _resolved: [_DummyTool("search_github_issues")],
    )
    monkeypatch.setattr(agent_llm_client, "get_agent_llm", object)

    executed = [
        (
            agent_llm_client.ToolCall(
                id="t1", name="search_github_issues", input={"owner": "o", "repo": "r"}
            ),
            {"issues": ["#1", "#2"]},
        )
    ]

    def _fake_loop(**_kwargs: Any) -> tool_loop.ToolLoopResult:
        return tool_loop.ToolLoopResult(messages=[], final_text="", executed=executed)

    monkeypatch.setattr(tool_loop, "run_tool_calling_loop", _fake_loop)

    observation = gather_tool_evidence("any open issues?", session, _console())

    assert observation is not None
    assert "search_github_issues" in observation
    assert '"owner": "o"' in observation
    assert '"repo": "r"' in observation


def test_no_executed_returns_none(monkeypatch: Any) -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(
        investigate_tools,
        "get_available_tools",
        lambda _resolved: [_DummyTool("search_github_issues")],
    )
    monkeypatch.setattr(agent_llm_client, "get_agent_llm", object)

    def _fake_loop(**_kwargs: Any) -> tool_loop.ToolLoopResult:
        return tool_loop.ToolLoopResult(messages=[], final_text="nothing to do", executed=[])

    monkeypatch.setattr(tool_loop, "run_tool_calling_loop", _fake_loop)

    assert gather_tool_evidence("any question", session, _console()) is None


def test_exception_path_returns_none(monkeypatch: Any) -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(
        investigate_tools,
        "get_available_tools",
        lambda _resolved: [_DummyTool("search_github_issues")],
    )

    def _boom() -> Any:
        raise RuntimeError("tool-calling client unavailable")

    monkeypatch.setattr(agent_llm_client, "get_agent_llm", _boom)

    assert gather_tool_evidence("any question", session, _console()) is None
