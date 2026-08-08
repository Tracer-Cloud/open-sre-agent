from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.benchmarks.orcabench.config import GrafanaSettings
from tests.benchmarks.orcabench.execution.native_connection import (
    OrcaGrafanaConnection,
)
from tests.benchmarks.orcabench.execution.task_context import parse_orca_task_context
from tools.investigation.stages.gather_evidence.tools import (
    build_seed_calls,
    get_available_tools,
    select_investigation_tools,
)
from tools.investigation.stages.plan_evidence.node import plan_actions
from tools.investigation.state_factory import make_initial_state


def _orca_instruction(issue: str = "users are reporting site issues") -> str:
    return (
        "# RCA\n\nYou are an expert site reliability engineer. "
        "The current time is Apr 21, 2026 at 09:00 ET.\n\n"
        "## Task Description\n\n"
        "The following issue was reported at 9:00 AM today ET:\n\n"
        f"{issue}\n\n"
        "NOTE:\n"
        "* The report time may differ from the incident time.\n\n"
        "## Telemetry Tools\n\n"
        "Use the Grafana HTTP API.\n"
    )


def test_parses_standard_orca_current_time_and_builds_historical_window() -> None:
    context = parse_orca_task_context(_orca_instruction())

    assert context.current_time.astimezone(UTC) == datetime(2026, 4, 21, 13, 0, tzinfo=UTC)
    assert context.reported_issue == "users are reporting site issues"
    assert context.incident_window() == {
        "_schema_version": 1,
        "since": "2026-04-21T11:00:00Z",
        "until": "2026-04-21T13:00:00Z",
        "source": "caller_override",
        "confidence": 1.0,
    }
    assert context.investigation_alert() == {
        "alert_source": "opensre_dataset",
        "commonAnnotations": {
            "summary": "users are reporting site issues",
            "context_sources": "grafana",
        },
    }


def test_rejects_missing_current_time_instead_of_using_host_clock() -> None:
    with pytest.raises(ValueError, match="missing its standardized current time"):
        parse_orca_task_context("users are reporting site issues")


def test_rejects_missing_reported_issue() -> None:
    with pytest.raises(ValueError, match="missing its 'Task Description' section"):
        parse_orca_task_context(
            "You are an expert SRE. The current time is Apr 21, 2026 at 09:00 ET."
        )


def test_orca_alert_plans_grafana_context_without_eager_seed_calls() -> None:
    context = parse_orca_task_context(_orca_instruction())
    integrations = OrcaGrafanaConnection(GrafanaSettings()).build(
        {"GRAFANA_URL": "http://grafana.invalid"},
        context.incident_window(),
    )
    state = dict(
        make_initial_state(
            context.investigation_alert(),
            incident_window=context.incident_window(),
        )
    )
    state["resolved_integrations"] = integrations

    plan = plan_actions(state)  # type: ignore[arg-type]
    state.update(plan)
    available = get_available_tools(integrations)
    agent_tools = select_investigation_tools(available, state)
    seed_calls = build_seed_calls(state, agent_tools, object())

    assert plan["plan_audit"]["matched_sources"] == ["grafana"]
    assert plan["planned_actions"] == [
        "query_grafana_alert_rules",
        "query_grafana_annotations",
        "query_grafana_logs",
        "query_grafana_metrics",
        "query_grafana_service_names",
        "query_grafana_traces",
    ]
    assert [tool.name for tool in agent_tools] == plan["planned_actions"]
    assert seed_calls == []
