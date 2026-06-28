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

import core as runtime_module
import core.agent as runtime_agent_module
import core.llm.agent_llm_client as agent_llm_client
import tools.investigation.stages.gather_evidence.tools as investigate_tools
from core.agent_harness.session import ReplSession
from core.llm.types import ToolCall
from tools.interactive_shell.tool_gathering import (
    _format_gathering_progress_line,
    _resolve_gather_integrations,
    _tool_input_hint,
    gather_tool_evidence,
)


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=80)


class _DummyTool:
    def __init__(self, name: str, source: str = "github") -> None:
        self.name = name
        self.source = source


def _patch_agent_run(monkeypatch: Any, run: Any) -> None:
    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def run(self, initial_messages: list[dict[str, Any]]) -> runtime_module.ToolLoopResult:
            return run(self.kwargs, initial_messages)

    monkeypatch.setattr(runtime_agent_module, "Agent", _FakeAgent)


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
            ToolCall(id="t1", name="search_github_issues", input={"owner": "o", "repo": "r"}),
            {"issues": ["#1", "#2"]},
        )
    ]

    def _fake_run(
        _kwargs: dict[str, Any], _initial_messages: list[dict[str, Any]]
    ) -> runtime_module.ToolLoopResult:
        return runtime_module.ToolLoopResult(messages=[], final_text="", executed=executed)

    _patch_agent_run(monkeypatch, _fake_run)

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

    def _fake_run(
        _kwargs: dict[str, Any], _initial_messages: list[dict[str, Any]]
    ) -> runtime_module.ToolLoopResult:
        return runtime_module.ToolLoopResult(messages=[], final_text="nothing to do", executed=[])

    _patch_agent_run(monkeypatch, _fake_run)

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


def test_tool_input_hint_prefers_distinguishing_fields() -> None:
    hint = _tool_input_hint(
        {
            "grafana_endpoint": "https://example.grafana.net",
            "metric_name": "sum(rate(http_requests_total[5m]))",
            "service_name": "checkout-api",
        }
    )
    assert hint == "sum(rate(http_requests_total[5m])) · checkout-api"


def test_format_gathering_progress_line_shows_repeat_index_and_hint() -> None:
    line = _format_gathering_progress_line(
        "query_grafana_metrics",
        {"metric_name": "pipeline_runs_total"},
        repeat_index=2,
    )
    assert line.startswith("· gathering via Grafana · Mimir (2) — pipeline_runs_total…")


def test_format_gathering_progress_line_escapes_display_and_hint_markup(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        "tools.interactive_shell.tool_gathering.tool_source_label",
        lambda _name: "Grafana [prod]",
    )
    monkeypatch.setattr(
        "tools.interactive_shell.tool_gathering.tool_short_label",
        lambda _name, _source: "Mimir",
    )

    line = _format_gathering_progress_line(
        "query_grafana_metrics",
        {"metric_name": "[critical] rate[5m]"},
        repeat_index=1,
    )
    console = _console()
    console.print(f"[dim]{line}[/]")

    output = console.file.getvalue()
    assert "Grafana [prod]" in output
    assert "[critical] rate[5m]" in output


def test_gathering_progress_lines_print_on_tool_start(monkeypatch: Any) -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {}
    console = _console()

    monkeypatch.setattr(
        investigate_tools,
        "get_available_tools",
        lambda _resolved: [_DummyTool("query_grafana_metrics", source="grafana")],
    )
    monkeypatch.setattr(agent_llm_client, "get_agent_llm", object)

    def _fake_run(
        kwargs: dict[str, Any], _initial_messages: list[dict[str, Any]]
    ) -> runtime_module.ToolLoopResult:
        on_runtime_event = kwargs.get("on_runtime_event")
        if on_runtime_event is not None:
            on_runtime_event(
                runtime_module.ToolExecutionStartEvent(
                    tool_call_id="t1",
                    tool_name="query_grafana_metrics",
                    args={"metric_name": "pipeline_runs_total"},
                    iteration=0,
                )
            )
            on_runtime_event(
                runtime_module.ToolExecutionStartEvent(
                    tool_call_id="t2",
                    tool_name="query_grafana_metrics",
                    args={"metric_name": "http_errors_total"},
                    iteration=0,
                )
            )
        return runtime_module.ToolLoopResult(messages=[], final_text="", executed=[])

    _patch_agent_run(monkeypatch, _fake_run)

    gather_tool_evidence("check metrics", session, console)
    output = console.file.getvalue()
    assert "Grafana · Mimir — pipeline_runs_total" in output
    assert "Grafana · Mimir (2) — http_errors_total" in output


def test_resolve_gather_integrations_enriches_github_from_repo_url() -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {
        "github": {"connection_verified": True, "url": "https://api.githubcopilot.com/mcp/"}
    }

    resolved = _resolve_gather_integrations(
        session,
        "check github issues in https://github.com/Tracer-Cloud/opensre",
    )

    gh = resolved["github"]
    assert gh["owner"] == "Tracer-Cloud"
    assert gh["repo"] == "opensre"
    assert session.github_repo_scope == ("Tracer-Cloud", "opensre")


def test_resolve_gather_integrations_uses_session_cache_on_follow_up() -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {
        "github": {"connection_verified": True, "url": "https://api.githubcopilot.com/mcp/"}
    }
    session.github_repo_scope = ("Tracer-Cloud", "opensre")
    session.agent.messages = [
        ("user", "https://github.com/Tracer-Cloud/opensre"),
        ("assistant", "Got it."),
    ]

    resolved = _resolve_gather_integrations(session, "do these searches")

    assert resolved["github"]["owner"] == "Tracer-Cloud"
    assert resolved["github"]["repo"] == "opensre"


def test_gather_enriches_github_before_selecting_tools(monkeypatch: Any) -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {
        "github": {"connection_verified": True, "url": "https://api.githubcopilot.com/mcp/"}
    }
    seen: dict[str, Any] = {}

    def _capture_tools(resolved: dict[str, Any]) -> list[_DummyTool]:
        seen["resolved"] = resolved
        gh = resolved.get("github", {})
        if isinstance(gh, dict) and gh.get("owner") and gh.get("repo"):
            return [_DummyTool("search_github_issues")]
        return []

    monkeypatch.setattr(investigate_tools, "get_available_tools", _capture_tools)
    monkeypatch.setattr(agent_llm_client, "get_agent_llm", object)

    def _fake_run(
        _kwargs: dict[str, Any], _initial_messages: list[dict[str, Any]]
    ) -> runtime_module.ToolLoopResult:
        return runtime_module.ToolLoopResult(messages=[], final_text="", executed=[])

    _patch_agent_run(monkeypatch, _fake_run)

    gather_tool_evidence(
        "check github issues in https://github.com/Tracer-Cloud/opensre",
        session,
        _console(),
    )

    gh = seen["resolved"]["github"]
    assert gh["owner"] == "Tracer-Cloud"
    assert gh["repo"] == "opensre"


def test_gather_user_message_includes_recent_conversation(monkeypatch: Any) -> None:
    session = ReplSession()
    session.resolved_integrations_cache = {}
    session.agent.messages = [("user", "prior question"), ("assistant", "prior answer")]
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        investigate_tools,
        "get_available_tools",
        lambda _resolved: [_DummyTool("search_github_issues")],
    )
    monkeypatch.setattr(agent_llm_client, "get_agent_llm", object)

    def _fake_run(
        _kwargs: dict[str, Any], initial_messages: list[dict[str, Any]]
    ) -> runtime_module.ToolLoopResult:
        captured["messages"] = initial_messages
        return runtime_module.ToolLoopResult(messages=[], final_text="", executed=[])

    _patch_agent_run(monkeypatch, _fake_run)

    gather_tool_evidence("follow up", session, _console())

    content = captured["messages"][0]["content"]
    assert "Recent conversation:" in content
    assert "prior question" in content
    assert "Current question:\nfollow up" in content
