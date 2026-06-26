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

import core.orchestration.node.investigate.tools as investigate_tools
import core.runtime as runtime_module
import services.agent_llm_client as agent_llm_client
from cli.interactive_shell.chat.tool_gathering import (
    _format_gathering_progress_line,
    _tool_input_hint,
    gather_tool_evidence,
)
from cli.interactive_shell.runtime.session import ReplSession


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

    def _fake_loop(**_kwargs: Any) -> runtime_module.ToolLoopResult:
        return runtime_module.ToolLoopResult(messages=[], final_text="", executed=executed)

    monkeypatch.setattr(runtime_module, "run_tool_calling_loop", _fake_loop)

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

    def _fake_loop(**_kwargs: Any) -> runtime_module.ToolLoopResult:
        return runtime_module.ToolLoopResult(messages=[], final_text="nothing to do", executed=[])

    monkeypatch.setattr(runtime_module, "run_tool_calling_loop", _fake_loop)

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
        "cli.interactive_shell.chat.tool_gathering.tool_source_label",
        lambda _name: "Grafana [prod]",
    )
    monkeypatch.setattr(
        "cli.interactive_shell.chat.tool_gathering.tool_short_label",
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

    def _fake_loop(**kwargs: Any) -> runtime_module.ToolLoopResult:
        on_event = kwargs.get("on_event")
        if on_event is not None:
            on_event(
                "tool_start",
                {
                    "id": "t1",
                    "name": "query_grafana_metrics",
                    "input": {"metric_name": "pipeline_runs_total"},
                },
            )
            on_event(
                "tool_start",
                {
                    "id": "t2",
                    "name": "query_grafana_metrics",
                    "input": {"metric_name": "http_errors_total"},
                },
            )
        return runtime_module.ToolLoopResult(messages=[], final_text="", executed=[])

    monkeypatch.setattr(runtime_module, "run_tool_calling_loop", _fake_loop)

    gather_tool_evidence("check metrics", session, console)
    output = console.file.getvalue()
    assert "Grafana · Mimir — pipeline_runs_total" in output
    assert "Grafana · Mimir (2) — http_errors_total" in output
