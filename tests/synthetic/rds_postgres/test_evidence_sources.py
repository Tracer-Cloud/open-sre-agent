"""Adversarial tests for the evidence_sources semantic predicate registry.

Phase 1 done-criteria: these tests prove that aws_cloudwatch_metrics and
aws_performance_insights are never conflated — scenarios requiring PI fail when
only CloudWatch evidence exists, and vice versa.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.synthetic.rds_postgres.evidence_sources import (
    EvidencePresence,
    EvidenceSourceId,
    evaluate,
    missing_sources,
)

# ---------------------------------------------------------------------------
# Helpers: fabricate final_state with specific evidence patterns
# ---------------------------------------------------------------------------


def _state_with_cloudwatch_only() -> dict[str, Any]:
    """Agent gathered CloudWatch metrics but NOT Performance Insights."""
    return {
        "root_cause": "CPU saturation caused by a long-running query.",
        "root_cause_category": "cpu_saturation",
        "evidence": {
            "aws_cloudwatch_metrics": {
                "db_instance_identifier": "payments-prod",
                "metrics": [
                    {"metric_name": "CPUUtilization", "values": [72.5, 85.0]},
                    {"metric_name": "DatabaseConnections", "values": [100, 120]},
                ],
                "observations": ["CPU is elevated at 85%"],
            },
            "grafana_metrics": [
                {"metric_name": "CPUUtilization", "values": [72.5, 85.0]},
            ],
        },
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "report": "CPU saturation is due to elevated CPU utilization.",
    }


def _state_with_pi_only() -> dict[str, Any]:
    """Agent gathered Performance Insights but NOT CloudWatch metrics."""
    return {
        "root_cause": "Bad query consuming 90% of AAS DB load.",
        "root_cause_category": "cpu_saturation",
        "evidence": {
            "aws_performance_insights": {
                "db_instance_identifier": "payments-prod",
                "observations": [
                    "Top SQL Activity: SELECT * FROM orders | Avg Load: 3.5 AAS | Waits: CPU"
                ],
                "top_sql": [{"sql": "SELECT * FROM orders", "db_load": 3.5, "wait_event": "CPU"}],
                "top_wait_events": [{"name": "CPU", "db_load": 3.5}],
                "db_load": {"timestamps": ["2024-01-01T00:00:00Z"], "values": [3.5]},
            },
        },
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "report": "Top SQL activity consuming 3.5 AAS avg load.",
    }


def _state_with_both() -> dict[str, Any]:
    """Agent gathered both CloudWatch and Performance Insights."""
    state = _state_with_cloudwatch_only()
    state["evidence"].update(_state_with_pi_only()["evidence"])
    return state


def _state_empty() -> dict[str, Any]:
    """Agent gathered no evidence."""
    return {
        "root_cause": "",
        "root_cause_category": "unknown",
        "evidence": {},
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "report": "",
    }


# ---------------------------------------------------------------------------
# Adversarial tests: PI required but only CloudWatch present
# ---------------------------------------------------------------------------


def test_pi_required_fails_when_only_cloudwatch_present() -> None:
    """evaluate([PI]) → present=False when only CloudWatch evidence exists."""
    state = _state_with_cloudwatch_only()
    results = evaluate(state, [EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS.value])
    assert len(results) == 1
    presence = results[0]
    assert isinstance(presence, EvidencePresence)
    assert presence.present is False, (
        f"Expected PI to be absent but got present=True; reason: {presence.reason}"
    )
    assert "no Performance Insights signal" in presence.reason


def test_cloudwatch_required_fails_when_only_pi_present() -> None:
    """evaluate([CW]) → present=False when only PI evidence exists."""
    state = _state_with_pi_only()
    results = evaluate(state, [EvidenceSourceId.AWS_CLOUDWATCH_METRICS.value])
    assert len(results) == 1
    presence = results[0]
    assert presence.present is False, (
        f"Expected CloudWatch to be absent but got present=True; reason: {presence.reason}"
    )
    assert "no CloudWatch evidence" in presence.reason


def test_both_required_pass_when_both_present() -> None:
    """evaluate([CW, PI]) → both present when both evidence keys populated."""
    state = _state_with_both()
    results = evaluate(
        state,
        [
            EvidenceSourceId.AWS_CLOUDWATCH_METRICS.value,
            EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS.value,
        ],
    )
    assert len(results) == 2
    cw_presence, pi_presence = results
    assert cw_presence.present is True, f"Expected CW present; reason: {cw_presence.reason}"
    assert pi_presence.present is True, f"Expected PI present; reason: {pi_presence.reason}"


def test_empty_evidence_fails_all() -> None:
    """All sources absent when evidence dict is empty."""
    state = _state_empty()
    results = evaluate(
        state,
        [
            EvidenceSourceId.AWS_CLOUDWATCH_METRICS.value,
            EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS.value,
            EvidenceSourceId.AWS_RDS_EVENTS.value,
        ],
    )
    assert all(not p.present for p in results), [p for p in results if p.present]


# ---------------------------------------------------------------------------
# missing_sources convenience wrapper
# ---------------------------------------------------------------------------


def test_missing_sources_returns_plain_strings() -> None:
    """missing_sources returns str values not enum instances (no <Enum...> repr noise)."""
    state = _state_with_cloudwatch_only()
    missing = missing_sources(state, [EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS.value])
    assert missing == ["aws_performance_insights"]
    assert all(type(m) is str for m in missing), "Expected plain str, not enum subtype"


def test_missing_sources_empty_when_all_present() -> None:
    state = _state_with_both()
    missing = missing_sources(
        state,
        [
            EvidenceSourceId.AWS_CLOUDWATCH_METRICS.value,
            EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS.value,
        ],
    )
    assert missing == []


# ---------------------------------------------------------------------------
# RDS events predicate
# ---------------------------------------------------------------------------


def test_rds_events_detected_via_grafana_logs() -> None:
    state = {
        "evidence": {
            "grafana_logs": [
                {"message": "Multi-AZ failover detected", "timestamp": "2024-01-01T00:00:00Z"}
            ]
        }
    }
    results = evaluate(state, [EvidenceSourceId.AWS_RDS_EVENTS.value])
    assert results[0].present is True


def test_rds_events_detected_via_direct_key() -> None:
    state = {
        "evidence": {"aws_rds_events": [{"message": "DB instance restarted", "date": "2024-01-01"}]}
    }
    results = evaluate(state, [EvidenceSourceId.AWS_RDS_EVENTS.value])
    assert results[0].present is True


def test_ec2_instances_detected_via_runtime_mapped_keys() -> None:
    state = {
        "evidence": {
            "ec2_instances": [{"instance_id": "i-123", "tier": "web"}],
            "ec2_instances_by_tier": {"web": ["i-123"]},
        }
    }
    results = evaluate(state, [EvidenceSourceId.EC2_INSTANCES_BY_TAG.value])
    assert results[0].present is True


def test_elb_target_health_detected_via_runtime_mapped_keys() -> None:
    state = {
        "evidence": {
            "elb_target_groups": [{"TargetGroupArn": "tg-1"}],
            "elb_healthy_targets": [{"instance_id": "i-123", "state": "healthy"}],
            "elb_target_health_summary": {"healthy_count": 1},
        }
    }
    results = evaluate(state, [EvidenceSourceId.ELB_TARGET_HEALTH.value])
    assert results[0].present is True


# ---------------------------------------------------------------------------
# PI via grafana_metrics text content (secondary check)
# ---------------------------------------------------------------------------


def test_pi_detected_via_grafana_metrics_pi_tokens() -> None:
    """PI is detected when grafana_metrics content contains PI-typed tokens."""
    state = {
        "evidence": {
            "grafana_metrics": [
                {
                    "top_sql": "SELECT * FROM orders",
                    "avg_load": 3.5,
                    "top_wait_events": [{"name": "CPU"}],
                }
            ]
        }
    }
    results = evaluate(state, [EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS.value])
    assert results[0].present is True


def test_cloudwatch_only_grafana_metrics_does_not_trigger_pi() -> None:
    """Pure CloudWatch data in grafana_metrics must NOT trigger PI detection."""
    state = {
        "evidence": {
            "grafana_metrics": [
                {"metric_name": "CPUUtilization", "values": [72.5, 85.0], "unit": "Percent"}
            ]
        }
    }
    cw_results = evaluate(state, [EvidenceSourceId.AWS_CLOUDWATCH_METRICS.value])
    pi_results = evaluate(state, [EvidenceSourceId.AWS_PERFORMANCE_INSIGHTS.value])

    assert cw_results[0].present is True, "CloudWatch should be detected via grafana_metrics"
    assert pi_results[0].present is False, "Pure CW data must NOT trigger PI detection"


# ---------------------------------------------------------------------------
# score_result integration: PI required but only CW present → passed=False
# ---------------------------------------------------------------------------


def test_score_result_fails_when_pi_required_but_only_cloudwatch_present() -> None:
    """Replaces the silent-pass that existed before Phase 1.

    A scenario requiring PI evidence must fail when only CloudWatch data is available,
    even if grafana_metrics is populated.
    """
    from tests.synthetic.rds_postgres.run_suite import score_result
    from tests.synthetic.rds_postgres.scenario_loader import SUITE_DIR, load_all_scenarios

    # Use scenario 004 (cpu-saturation-bad-query) which requires PI
    fixtures = load_all_scenarios(SUITE_DIR)
    pi_fixture = next(
        (
            f
            for f in fixtures
            if "aws_performance_insights" in (f.answer_key.required_evidence_sources or [])
        ),
        None,
    )
    if pi_fixture is None:
        pytest.skip("No fixture requires aws_performance_insights")

    # Supply only CloudWatch evidence, no PI
    cw_only_state = {
        "root_cause": pi_fixture.answer_key.root_cause_category,
        "root_cause_category": pi_fixture.answer_key.root_cause_category,
        "evidence": {
            "aws_cloudwatch_metrics": {
                "metrics": [{"metric_name": "CPUUtilization", "values": [95.0]}],
                "observations": ["CPU is elevated"],
            },
            "grafana_metrics": [{"metric_name": "CPUUtilization", "values": [95.0]}],
        },
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "report": " ".join(pi_fixture.answer_key.required_keywords),
    }

    score = score_result(pi_fixture, cw_only_state)
    assert score.passed is False, (
        f"Expected FAIL for {pi_fixture.scenario_id}: "
        "PI required but only CloudWatch supplied; before Phase 1 this was a silent pass"
    )
    missing = next(
        (r for r in (score.failure_reasons or []) if r.code == "MISSING_REQUIRED_EVIDENCE_SOURCE"),
        None,
    )
    assert missing is not None
    assert "aws_performance_insights" in missing.detail


@pytest.mark.parametrize(
    "source_id,evidence_key",
    [
        (EvidenceSourceId.K8S_EVENTS.value, "k8s_events"),
        (EvidenceSourceId.K8S_POD_METRICS.value, "k8s_pod_metrics"),
        (EvidenceSourceId.K8S_NODE_METRICS.value, "k8s_node_metrics"),
        (EvidenceSourceId.K8S_DNS_METRICS.value, "k8s_dns_metrics"),
        (EvidenceSourceId.K8S_MESH_METRICS.value, "k8s_mesh_metrics"),
        (EvidenceSourceId.K8S_ROLLOUT.value, "k8s_rollout"),
    ],
)
def test_k8s_semantic_sources_detect_presence(source_id: str, evidence_key: str) -> None:
    state = {"evidence": {evidence_key: {"sample": "value"}}}
    presence = evaluate(state, [source_id])[0]
    assert presence.present is True
    assert presence.source_id == source_id


@pytest.mark.parametrize(
    "source_id",
    [
        EvidenceSourceId.K8S_EVENTS.value,
        EvidenceSourceId.K8S_POD_METRICS.value,
        EvidenceSourceId.K8S_NODE_METRICS.value,
        EvidenceSourceId.K8S_DNS_METRICS.value,
        EvidenceSourceId.K8S_MESH_METRICS.value,
        EvidenceSourceId.K8S_ROLLOUT.value,
    ],
)
def test_k8s_semantic_sources_report_missing_when_absent(source_id: str) -> None:
    assert missing_sources({"evidence": {}}, [source_id]) == [source_id]
