from __future__ import annotations

from app.nodes.root_cause_diagnosis.prompt_builder import build_diagnosis_prompt


def _rds_state() -> dict[str, object]:
    return {
        "problem_md": "CPUUtilization on catalog-prod is above threshold.",
        "alert_name": "RDSCPUUtilizationHigh",
        "pipeline": "rds-postgres-synthetic",
        "raw_alert": {
            "commonAnnotations": {
                "summary": "RDS PostgreSQL database CPU saturation on catalog-prod",
            },
        },
        "hypotheses": [],
    }


def test_performance_insights_fixture_fields_are_rendered() -> None:
    evidence = {
        "aws_performance_insights": {
            "db_instance_identifier": "catalog-prod",
            "top_sql": [
                {
                    "statement": "SELECT * FROM orders WHERE status = $1",
                    "db_load_avg": 7.2,
                    "wait_events": [
                        {"name": "CPU:user", "type": "CPU", "db_load_avg": 5.9},
                        {"name": "IO:DataFileRead", "type": "IO", "db_load_avg": 1.3},
                    ],
                    "calls_per_sec": 3.8,
                }
            ],
            "top_wait_events": [
                {"name": "CPU:user", "type": "CPU", "db_load_avg": 6.2},
            ],
            "top_users": [
                {"name": "api_reader", "db_load_avg": 7.2},
            ],
            "top_hosts": [
                {"id": "10.0.2.71", "db_load_avg": 4.1},
            ],
        }
    }

    prompt = build_diagnosis_prompt(_rds_state(), evidence)

    assert "Performance Insights:" in prompt
    assert "sql=SELECT * FROM orders WHERE status = $1" in prompt
    assert "db_load=7.2" in prompt
    assert "wait_events=CPU:user(5.9), IO:DataFileRead(1.3)" in prompt
    assert "calls_per_sec=3.8" in prompt
    assert "- CPU:user [CPU] | db_load=6.2" in prompt
    assert "- api_reader | db_load=7.2" in prompt
    assert "- 10.0.2.71 | db_load=4.1" in prompt


def test_grafana_logs_show_performance_insights_source() -> None:
    evidence = {
        "grafana_logs": [
            {
                "message": (
                    "Top SQL Activity: SELECT * FROM orders WHERE status = $1 "
                    "| Avg Load: 7.2 AAS | Waits: CPU:user(5.9)"
                ),
                "source_type": "aws_performance_insights",
                "source_identifier": "catalog-prod",
            }
        ]
    }

    prompt = build_diagnosis_prompt(_rds_state(), evidence)

    assert "[Performance Insights catalog-prod] Top SQL Activity" in prompt
    assert "SELECT * FROM orders WHERE status = $1" in prompt


def test_grafana_error_logs_show_performance_insights_source() -> None:
    evidence = {
        "grafana_error_logs": [
            {
                "message": "Top Wait Event: CPU:user | db_load_avg: 6.2 AAS",
                "source_type": "aws_performance_insights",
                "source_identifier": "catalog-prod",
            }
        ]
    }

    prompt = build_diagnosis_prompt(_rds_state(), evidence)

    assert "[Performance Insights catalog-prod] Top Wait Event" in prompt


def test_unknown_grafana_source_type_is_not_rendered() -> None:
    evidence = {
        "grafana_logs": [
            {
                "message": "Internal telemetry event",
                "source_type": "custom_internal_source",
                "source_identifier": "catalog-prod",
            }
        ]
    }

    prompt = build_diagnosis_prompt(_rds_state(), evidence)

    assert "[catalog-prod] Internal telemetry event" in prompt
    assert "custom_internal_source" not in prompt


def test_database_directive_requires_exact_bad_query_reasoning() -> None:
    prompt = build_diagnosis_prompt(_rds_state(), {"grafana_logs": []})

    assert "name the exact SQL statement from Performance Insights" in prompt
    assert "explicitly rule out connection exhaustion" in prompt
