from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from context.state import AgentState
from core.domain.types.retrieval import RetrievalControls
from tools.investigation.stages.plan_evidence.node import plan_actions
from tools.registered_tool import RegisteredTool


def _tool(
    name: str,
    source: str,
    *,
    description: str = "",
    use_cases: list[str] | None = None,
    retrieval_controls: RetrievalControls | None = None,
) -> RegisteredTool:
    def _run(**_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    return RegisteredTool(
        name=name,
        description=description or f"{name} tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        source=source,  # type: ignore[arg-type]
        run=cast(Callable[..., Any], _run),
        use_cases=use_cases or [],
        retrieval_controls=retrieval_controls or RetrievalControls(),
    )


def test_plan_actions_prioritizes_alert_source_tools(monkeypatch: Any) -> None:
    tools = [
        _tool("query_datadog_logs", "datadog"),
        _tool("query_github_commits", "github"),
        _tool("get_sre_guidance", "knowledge"),
    ]
    monkeypatch.setattr(
        "tools.investigation.stages.plan_evidence.node.get_registered_tools", lambda _s: tools
    )

    result = plan_actions(
        cast(
            AgentState,
            {
                "alert_source": "datadog",
                "resolved_integrations": {
                    "datadog": {"api_key": "x"},
                    "github": {"token": "y"},
                },
                "tool_budget": 2,
            },
        )
    )

    assert result["planned_actions"][0] == "query_datadog_logs"
    assert "datadog" in result["plan_rationale"]
    assert result["plan_audit"]["selected"][0]["source"] == "datadog"


def test_plan_actions_uses_context_sources_for_generic_alert(monkeypatch: Any) -> None:
    tools = [
        _tool("query_datadog_logs", "datadog"),
        _tool("query_github_commits", "github"),
    ]
    monkeypatch.setattr(
        "tools.investigation.stages.plan_evidence.node.get_registered_tools", lambda _s: tools
    )

    result = plan_actions(
        cast(
            AgentState,
            {
                "alert_source": "generic",
                "raw_alert": {"commonAnnotations": {"context_sources": "github"}},
                "resolved_integrations": {
                    "datadog": {"api_key": "x"},
                    "github": {"token": "y"},
                },
            },
        )
    )

    assert result["planned_actions"][0] == "query_github_commits"
    assert result["plan_audit"]["matched_sources"] == ["github"]


def test_plan_actions_applies_budget_and_records_exclusions(monkeypatch: Any) -> None:
    tools = [
        _tool("query_datadog_logs", "datadog"),
        _tool("query_datadog_metrics", "datadog"),
        _tool("query_datadog_traces", "datadog"),
    ]
    monkeypatch.setattr(
        "tools.investigation.stages.plan_evidence.node.get_registered_tools", lambda _s: tools
    )

    result = plan_actions(
        cast(
            AgentState,
            {
                "alert_source": "datadog",
                "resolved_integrations": {"datadog": {"api_key": "x"}},
                "tool_budget": 2,
            },
        )
    )

    assert len(result["planned_actions"]) == 2
    assert len(result["plan_audit"]["excluded"]) == 1


def test_plan_actions_populates_supported_retrieval_controls(monkeypatch: Any) -> None:
    tools = [
        _tool(
            "query_datadog_logs",
            "datadog",
            retrieval_controls=RetrievalControls(time_bounds=True, limit=True),
        )
    ]
    monkeypatch.setattr(
        "tools.investigation.stages.plan_evidence.node.get_registered_tools", lambda _s: tools
    )

    result = plan_actions(
        cast(
            AgentState,
            {
                "alert_source": "datadog",
                "resolved_integrations": {"datadog": {"api_key": "x"}},
                "incident_window": {
                    "since": "2026-06-22T14:00:00Z",
                    "until": "2026-06-22T15:00:00Z",
                },
            },
        )
    )

    controls = result["retrieval_controls"]
    assert controls["query_datadog_logs"].time_bounds is not None
    assert controls["query_datadog_logs"].limit == 100


def test_plan_actions_uses_guidance_fallback_when_nothing_matches(monkeypatch: Any) -> None:
    tools = [
        _tool("query_github_commits", "github"),
        _tool("get_sre_guidance", "knowledge"),
    ]
    monkeypatch.setattr(
        "tools.investigation.stages.plan_evidence.node.get_registered_tools", lambda _s: tools
    )

    result = plan_actions(
        cast(
            AgentState,
            {
                "alert_source": "generic",
                "message": "mysterious failure",
                "resolved_integrations": {"github": {"token": "y"}},
            },
        )
    )

    assert result["planned_actions"] == ["get_sre_guidance"]
    assert "fallback" in " ".join(result["plan_audit"]["selected"][0]["reasons"])
