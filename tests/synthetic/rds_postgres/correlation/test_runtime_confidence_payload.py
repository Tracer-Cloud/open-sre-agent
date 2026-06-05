from app.agent.correlation.runtime import build_runtime_correlation
from app.agent.correlation.upstream import MetricSeries, TopologyHint, UpstreamEvidenceBundle


def test_runtime_payload_includes_shared_confidence_evidence_breakdown() -> None:
    evidence = UpstreamEvidenceBundle(
        rds_metrics=(
            MetricSeries(
                source="datadog",
                name="rds.cpu",
                timestamps=("2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"),
                values=(10.0, 90.0),
            ),
        ),
        upstream_metrics=(
            MetricSeries(
                source="datadog",
                name="checkout-web.latency",
                timestamps=("2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"),
                values=(20.0, 95.0),
            ),
        ),
        topology_hints=(
            TopologyHint(
                source="checkout-web.latency",
                target="rds-main",
                relation="upstream_of",
            ),
        ),
        operator_hints=("scheduled checkout workflow recently shipped",),
    )

    payload = build_runtime_correlation(evidence, target_resource="rds-main")

    driver = payload["most_likely_causal_drivers"][0]

    assert driver["confidence_label"] in {"medium", "high"}
    assert driver["evidence_breakdown"]
    assert {item["source"] for item in driver["evidence_breakdown"]} == {
        "correlation",
        "topology",
        "periodicity",
        "feature_workflow",
    }
    assert any(
        item["source"] == "feature_workflow" and item["score"] == 1.0
        for item in driver["evidence_breakdown"]
    )


def test_runtime_uses_file_based_feature_workflow_config() -> None:
    evidence = UpstreamEvidenceBundle(
        rds_metrics=(
            MetricSeries(
                source="datadog",
                name="rds.cpu",
                timestamps=("2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"),
                values=(10.0, 90.0),
            ),
        ),
        upstream_metrics=(
            MetricSeries(
                source="datadog",
                name="checkout-worker.latency",
                timestamps=("2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"),
                values=(20.0, 95.0),
            ),
        ),
        topology_hints=(
            TopologyHint(
                source="checkout-worker.latency",
                target="rds-main",
                relation="upstream_of",
            ),
        ),
        operator_hints=(
            "/checkout/retry",
            "scheduled_workflow recently_shipped checkout_retry_workflow",
        ),
    )

    payload = build_runtime_correlation(evidence, target_resource="rds-main")
    driver = payload["most_likely_causal_drivers"][0]

    feature_entry = next(
        item for item in driver["evidence_breakdown"] if item["source"] == "feature_workflow"
    )

    assert feature_entry["score"] == 1.0
    assert "feature/workflow" in str(feature_entry["rationale"]).lower()
