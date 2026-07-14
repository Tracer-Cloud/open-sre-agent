"""Tests for the interactive-shell tool-gathering pass.

``gather_integration_tool_evidence`` runs a bounded tool-calling loop over the same
registered tools the investigation uses and returns the collected outputs as a
formatted observation block (or ``None`` when there is nothing to add). These
tests exercise the no-tools, executed-results, no-executed, and exception paths
without any live LLM by stubbing ``agent_factory`` and monkeypatching tool
discovery / LLM load where needed.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from rich.console import Console

import core as runtime_module
import platform.harness_ports as harness_ports
from core.agent_harness.turns.evidence_driver import GatherAgentFactory
from core.llm.types import ToolCall
from surfaces.interactive_shell.runtime.integration_tool_gathering import (
    _format_gathering_progress_line,
    _resolve_gather_integrations,
    _tool_input_hint,
    gather_integration_tool_evidence,
)
from surfaces.interactive_shell.session import Session

_FakeRun = Callable[[dict[str, Any], list[dict[str, Any]]], runtime_module.AgentRunResult]


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=80)


class _DummyTool:
    def __init__(self, name: str, source: str = "github") -> None:
        self.name = name
        self.source = source


def _stub_agent_factory(run: _FakeRun) -> GatherAgentFactory:
    """Return a factory that runs real gather setup but stubs ``Agent.run``."""

    class _StubAgent:
        def __init__(self, on_runtime_event: Any) -> None:
            self._on_runtime_event = on_runtime_event

        def run(self, initial_messages: list[dict[str, Any]]) -> runtime_module.AgentRunResult:
            kwargs = {"on_runtime_event": self._on_runtime_event}
            return run(kwargs, initial_messages)

    def factory(
        *,
        llm: Any,
        session: Session,
        gather_tools: list[Any],
        resolved: dict[str, Any],
        on_progress: Any,
    ) -> _StubAgent:
        _ = (llm, session, gather_tools, resolved)
        from core.events import runtime_event_callback_from_observer

        return _StubAgent(runtime_event_callback_from_observer(on_progress))

    return factory


def test_no_tools_available_returns_none(monkeypatch: Any) -> None:
    session = Session()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(harness_ports, "get_investigation_tools", lambda _resolved: [])

    assert gather_integration_tool_evidence("any question", session, _console()) is None


def test_secondary_only_tools_return_none(monkeypatch: Any) -> None:
    session = Session()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(
        harness_ports,
        "get_investigation_tools",
        lambda _resolved: [_DummyTool("get_sre_guidance", source="knowledge")],
    )

    def _unexpected_llm() -> Any:
        raise AssertionError("knowledge-only tools should not invoke the gather loop")

    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: _unexpected_llm())

    assert gather_integration_tool_evidence("why did it fail?", session, _console()) is None


def test_executed_results_return_formatted_observation(monkeypatch: Any) -> None:
    session = Session()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(
        harness_ports,
        "get_investigation_tools",
        lambda _resolved: [_DummyTool("search_github_issues")],
    )
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: object())

    executed = [
        (
            ToolCall(id="t1", name="search_github_issues", input={"owner": "o", "repo": "r"}),
            {"issues": ["#1", "#2"]},
        )
    ]

    def _fake_run(
        _kwargs: dict[str, Any], _initial_messages: list[dict[str, Any]]
    ) -> runtime_module.AgentRunResult:
        return runtime_module.AgentRunResult(messages=[], final_text="", executed=executed)

    observation = gather_integration_tool_evidence(
        "any open issues?",
        session,
        _console(),
        agent_factory=_stub_agent_factory(_fake_run),
    )

    assert observation is not None
    assert "search_github_issues" in observation
    assert '"owner": "o"' in observation
    assert '"repo": "r"' in observation


def test_no_executed_returns_none(monkeypatch: Any) -> None:
    session = Session()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(
        harness_ports,
        "get_investigation_tools",
        lambda _resolved: [_DummyTool("search_github_issues")],
    )
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: object())

    def _fake_run(
        _kwargs: dict[str, Any], _initial_messages: list[dict[str, Any]]
    ) -> runtime_module.AgentRunResult:
        return runtime_module.AgentRunResult(messages=[], final_text="nothing to do", executed=[])

    assert (
        gather_integration_tool_evidence(
            "any question",
            session,
            _console(),
            agent_factory=_stub_agent_factory(_fake_run),
        )
        is None
    )


def test_exception_path_returns_none(monkeypatch: Any) -> None:
    session = Session()
    session.resolved_integrations_cache = {}

    monkeypatch.setattr(
        harness_ports,
        "get_investigation_tools",
        lambda _resolved: [_DummyTool("search_github_issues")],
    )

    def _boom() -> Any:
        raise RuntimeError("tool-calling client unavailable")

    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: _boom())

    assert gather_integration_tool_evidence("any question", session, _console()) is None


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
        "surfaces.interactive_shell.runtime.integration_tool_gathering.tool_source_label",
        lambda _name: "Grafana [prod]",
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.integration_tool_gathering.tool_short_label",
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
    session = Session()
    session.resolved_integrations_cache = {}
    console = _console()

    monkeypatch.setattr(
        harness_ports,
        "get_investigation_tools",
        lambda _resolved: [_DummyTool("query_grafana_metrics", source="grafana")],
    )
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: object())

    def _fake_run(
        kwargs: dict[str, Any], _initial_messages: list[dict[str, Any]]
    ) -> runtime_module.AgentRunResult:
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
        return runtime_module.AgentRunResult(messages=[], final_text="", executed=[])

    gather_integration_tool_evidence(
        "check metrics",
        session,
        console,
        agent_factory=_stub_agent_factory(_fake_run),
    )
    output = console.file.getvalue()
    assert "Grafana · Mimir — pipeline_runs_total" in output
    assert "Grafana · Mimir (2) — http_errors_total" in output


def test_resolve_gather_integrations_enriches_github_from_repo_url() -> None:
    session = Session()
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
    session = Session()
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


def test_resolve_gather_integrations_enriches_gitlab_file_scope() -> None:
    session = Session()
    session.resolved_integrations_cache = {
        "gitlab": {
            "connection_verified": True,
            "base_url": "https://gitlab.com/api/v4",
            "auth_token": "token",
        }
    }

    resolved = _resolve_gather_integrations(
        session,
        "read https://gitlab.com/group/project/-/blob/main/runbooks/api.md",
    )

    gitlab = resolved["gitlab"]
    assert gitlab["project_id"] == "group/project"
    assert gitlab["ref_name"] == "main"
    assert gitlab["file_path"] == "runbooks/api.md"
    assert session.gitlab_repo_scope == ("group/project", "main", "runbooks/api.md")


def test_resolve_gather_integrations_uses_gitlab_session_cache() -> None:
    session = Session()
    session.resolved_integrations_cache = {
        "gitlab": {"connection_verified": True, "auth_token": "token"}
    }
    session.gitlab_repo_scope = ("group/project", "main", "runbook.md")

    resolved = _resolve_gather_integrations(session, "read that file")

    assert resolved["gitlab"]["project_id"] == "group/project"
    assert resolved["gitlab"]["file_path"] == "runbook.md"


def test_resolve_gather_integrations_uses_passed_turn_view() -> None:
    """When the turn's resolved view is supplied, it is the base — no session re-resolve."""
    session = Session()
    # The session cache holds a different integration than the turn resolved this turn.
    session.resolved_integrations_cache = {"datadog": {"connection_verified": True}}
    turn_resolved = {"slack": {"connection_verified": True}}

    resolved = _resolve_gather_integrations(
        session, "post an update", resolved_integrations=turn_resolved
    )

    assert resolved == {"slack": {"connection_verified": True}}


def test_resolve_gather_integrations_applies_github_scope_over_passed_view() -> None:
    """GitHub repo scope is still enriched on top of the passed turn view."""
    session = Session()
    session.resolved_integrations_cache = {}
    turn_resolved = {
        "github": {"connection_verified": True, "url": "https://api.githubcopilot.com/mcp/"}
    }

    resolved = _resolve_gather_integrations(
        session,
        "check github issues in https://github.com/Tracer-Cloud/opensre",
        resolved_integrations=turn_resolved,
    )

    assert resolved["github"]["owner"] == "Tracer-Cloud"
    assert resolved["github"]["repo"] == "opensre"


def test_gather_enriches_github_before_selecting_tools(monkeypatch: Any) -> None:
    session = Session()
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

    monkeypatch.setattr(harness_ports, "get_investigation_tools", _capture_tools)
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: object())

    def _fake_run(
        _kwargs: dict[str, Any], _initial_messages: list[dict[str, Any]]
    ) -> runtime_module.AgentRunResult:
        return runtime_module.AgentRunResult(messages=[], final_text="", executed=[])

    gather_integration_tool_evidence(
        "check github issues in https://github.com/Tracer-Cloud/opensre",
        session,
        _console(),
        agent_factory=_stub_agent_factory(_fake_run),
    )

    gh = seen["resolved"]["github"]
    assert gh["owner"] == "Tracer-Cloud"
    assert gh["repo"] == "opensre"


def test_gather_enriches_gitlab_before_selecting_tools(monkeypatch: Any) -> None:
    session = Session()
    session.resolved_integrations_cache = {
        "gitlab": {"connection_verified": True, "auth_token": "token"}
    }
    seen: dict[str, Any] = {}

    def _capture_tools(resolved: dict[str, Any]) -> list[_DummyTool]:
        seen["resolved"] = resolved
        gitlab = resolved.get("gitlab", {})
        if isinstance(gitlab, dict) and gitlab.get("file_path"):
            return [_DummyTool("get_gitlab_file", source="gitlab")]
        return []

    monkeypatch.setattr(harness_ports, "get_investigation_tools", _capture_tools)
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: object())

    def _fake_run(
        _kwargs: dict[str, Any], _initial_messages: list[dict[str, Any]]
    ) -> runtime_module.AgentRunResult:
        return runtime_module.AgentRunResult(messages=[], final_text="", executed=[])

    gather_integration_tool_evidence(
        "read https://gitlab.com/group/project/-/blob/main/runbook.md",
        session,
        _console(),
        agent_factory=_stub_agent_factory(_fake_run),
    )

    gitlab = seen["resolved"]["gitlab"]
    assert gitlab["project_id"] == "group/project"
    assert gitlab["file_path"] == "runbook.md"


def test_gather_user_message_includes_recent_conversation(monkeypatch: Any) -> None:
    session = Session()
    session.resolved_integrations_cache = {}
    session.agent.messages = [("user", "prior question"), ("assistant", "prior answer")]
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        harness_ports,
        "get_investigation_tools",
        lambda _resolved: [_DummyTool("search_github_issues")],
    )
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: object())

    def _fake_run(
        _kwargs: dict[str, Any], initial_messages: list[dict[str, Any]]
    ) -> runtime_module.AgentRunResult:
        captured["messages"] = initial_messages
        return runtime_module.AgentRunResult(messages=[], final_text="", executed=[])

    gather_integration_tool_evidence(
        "follow up",
        session,
        _console(),
        agent_factory=_stub_agent_factory(_fake_run),
    )

    content = captured["messages"][0]["content"]
    assert "Recent conversation:" in content
    assert "prior question" in content
    assert "Current question:\nfollow up" in content
