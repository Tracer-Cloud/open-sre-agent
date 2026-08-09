from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.benchmarks.orcabench.config import GrafanaSettings
from tests.benchmarks.orcabench.execution.native_connection import OrcaNativeConnections
from tests.benchmarks.orcabench.execution.native_investigation import (
    _build_orca_investigation_system_prompt,
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
        "* The time that an issue was reported is not necessarily the same as "
        "the time the incident actually began. Your task is to pinpoint the root "
        "cause events and the times of their occurrence.\n"
        "* There may be multiple root causes or no root cause at all.\n\n"
        "## Telemetry Tools\n\n"
        "Use the Grafana HTTP API.\n\n"
        "## Instructions\n\n"
        "First determine whether an incident actually occurred. If no incident "
        "occurred, write an empty report.\n\n"
        "## Section 1: Summary\n\n"
        "Summarize what happened.\n\n"
        "## Section 2: Timeline\n\n"
        "Give UTC events in chronological order.\n\n"
        "## Section 3: 5 Whys\n\n"
        "Ground each why in telemetry.\n\n"
        "## Section 4: Remediation\n\n"
        "Classify each corrective action.\n"
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
        "_meta": {
            "orca_investigation_guidance": (
                "The time that an issue was reported is not necessarily the same as "
                "the time the incident actually began. Your task is to pinpoint the "
                "root cause events and the times of their occurrence.\n"
                "There may be multiple root causes or no root cause at all."
            ),
            "orca_report_instructions": (
                "First determine whether an incident actually occurred. If no incident "
                "occurred, write an empty report.\n\n"
                "## Section 1: Summary\n\n"
                "Summarize what happened.\n\n"
                "## Section 2: Timeline\n\n"
                "Give UTC events in chronological order.\n\n"
                "## Section 3: 5 Whys\n\n"
                "Ground each why in telemetry.\n\n"
                "## Section 4: Remediation\n\n"
                "Classify each corrective action."
            ),
        },
        "commonAnnotations": {
            "summary": "users are reporting site issues",
            "context_sources": "grafana,local_source",
        },
    }


def test_orca_investigation_guidance_reaches_native_agent_context() -> None:
    context = parse_orca_task_context(_orca_instruction())
    state = dict(
        make_initial_state(
            context.investigation_alert(),
            incident_window=context.incident_window(),
        )
    )

    prompt = _build_orca_investigation_system_prompt(state)

    assert "## ORCA task guidance" in prompt
    assert "The time that an issue was reported" in prompt
    assert "There may be multiple root causes or no root cause at all." in prompt
    assert "## ORCA report contract" in prompt
    assert "## Section 3: 5 Whys" in prompt
    assert "Classify each corrective action." in prompt
    assert "## What to produce at the end" not in prompt
    assert "Incident command summary" not in prompt
    assert "Phase 4 — Mitigation" in prompt
    assert "using the ORCA report contract below" in prompt
    assert "Use the Grafana HTTP API" not in prompt


def test_rejects_missing_current_time_instead_of_using_host_clock() -> None:
    with pytest.raises(ValueError, match="missing its standardized current time"):
        parse_orca_task_context("users are reporting site issues")


def test_rejects_missing_reported_issue() -> None:
    with pytest.raises(ValueError, match="missing its 'Task Description' section"):
        parse_orca_task_context(
            "You are an expert SRE. The current time is Apr 21, 2026 at 09:00 ET."
        )


def test_rejects_missing_report_instructions() -> None:
    instruction_without_contract = _orca_instruction().partition("## Instructions")[0]

    with pytest.raises(ValueError, match="missing its 'Instructions' section"):
        parse_orca_task_context(instruction_without_contract)


def test_orca_alert_plans_telemetry_and_source_without_eager_seed_calls(
    tmp_path: Path,
) -> None:
    context = parse_orca_task_context(_orca_instruction())
    source_root = tmp_path / "opentelemetry-demo"
    source_root.mkdir()
    integrations = OrcaNativeConnections(GrafanaSettings(), source_root).build(
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

    assert plan["plan_audit"]["matched_sources"] == ["grafana", "local_source"]
    assert set(plan["planned_actions"]) == {
        "list_local_source_tree",
        "read_local_source_file",
        "search_local_source",
        "query_grafana_alert_rules",
        "query_grafana_annotations",
        "query_grafana_logs",
        "query_grafana_metrics",
        "query_grafana_service_names",
        "query_grafana_traces",
    }
    assert {tool.name for tool in agent_tools} == set(plan["planned_actions"])
    assert seed_calls == []
