from __future__ import annotations

from app.nodes.investigate.execution.execute_actions import ActionExecutionResult
from app.nodes.investigate.processing.post_process import merge_evidence
from app.nodes.root_cause_diagnosis.prompt_builder import build_diagnosis_prompt
from app.tools.GrafanaAlertRulesTool import query_grafana_alert_rules
from app.tools.GrafanaLogsTool import query_grafana_logs
from app.tools.GrafanaMetricsTool import query_grafana_metrics
from tests.synthetic.mock_grafana_backend.backend import FixtureGrafanaBackend
from tests.synthetic.rds_postgres.scenario_loader import SUITE_DIR, load_scenario


def test_rds_storage_fixture_prompt_surfaces_structured_grafana_evidence() -> None:
    fixture = load_scenario(SUITE_DIR / "003-storage-full")
    backend = FixtureGrafanaBackend(fixture)

    execution_results = {
        "query_grafana_metrics": ActionExecutionResult(
            "query_grafana_metrics",
            True,
            query_grafana_metrics(
                metric_name="pipeline_runs_total",
                service_name="rds-postgres-synthetic",
                grafana_backend=backend,
            ),
        ),
        "query_grafana_logs": ActionExecutionResult(
            "query_grafana_logs",
            True,
            query_grafana_logs(
                service_name="rds-postgres-synthetic",
                grafana_backend=backend,
            ),
        ),
        "query_grafana_alert_rules": ActionExecutionResult(
            "query_grafana_alert_rules",
            True,
            query_grafana_alert_rules(
                folder="rds-postgres-synthetic",
                grafana_backend=backend,
            ),
        ),
    }
    evidence = merge_evidence({}, execution_results)

    assert "aws_cloudwatch_metrics" in evidence
    assert "aws_rds_events" in evidence
    assert "aws_performance_insights" in evidence
    assert evidence["grafana_alert_rules_count"] == 1

    state = {
        "problem_md": fixture.problem_md,
        "hypotheses": [],
        "raw_alert": fixture.alert,
        "alert_name": fixture.alert["title"],
        "pipeline": "rds-postgres-synthetic",
    }
    prompt = build_diagnosis_prompt(state, evidence)

    assert "RDS CloudWatch Metrics:" in prompt
    assert "FreeStorageSpace" in prompt
    assert "WriteIOPS" in prompt
    assert "orders-prod" in prompt
    assert "DB instance ran out of storage space" in prompt
    assert "Performance Insights:" in prompt
    assert "INSERT INTO order_archive" in prompt
    assert "RDSFreeStorageSpaceLow" in prompt


def test_grafana_rds_event_timestamp_handles_float_nanoseconds() -> None:
    evidence = merge_evidence(
        {},
        {
            "query_grafana_logs": ActionExecutionResult(
                "query_grafana_logs",
                True,
                {
                    "logs": [
                        {
                            "timestamp": 1_774_577_553_000_000_000.0,
                            "message": "DB instance ran out of storage space.",
                            "source_type": "db-instance",
                            "source_identifier": "orders-prod",
                        }
                    ],
                    "error_logs": [],
                    "query": "",
                    "service_name": "rds-postgres-synthetic",
                },
            )
        },
    )

    assert evidence["aws_rds_events"][0]["timestamp"] == "2026-03-27T02:12:33Z"
