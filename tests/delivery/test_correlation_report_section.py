from __future__ import annotations

from app.delivery.publish_findings.formatters.report import (
    build_slack_blocks,
    format_slack_message,
)
from app.delivery.publish_findings.report_context import build_report_context


def test_publish_report_includes_upstream_correlation_section() -> None:
    state = {
        "alert_name": "RDS CPU spike",
        "pipeline_name": "rds-postgres",
        "severity": "warning",
        "root_cause": "The orders web tier is driving the RDS CPU spike.",
        "root_cause_category": "application_tier_load_spike",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
        "available_sources": {},
        "investigation_started_at": 0.0,
        "correlation": {
            "correlated_signals": [
                {
                    "source": "datadog",
                    "name": "orders-web-asg CPU",
                    "score": 0.95,
                }
            ],
            "most_likely_causal_drivers": [
                {
                    "name": "orders-web-asg",
                    "confidence": 0.91,
                    "rationale": "Time-aligned with RDS CPU and topology-adjacent.",
                }
            ],
        },
    }

    ctx = build_report_context(state)
    message = format_slack_message(ctx)
    blocks = build_slack_blocks(ctx)

    assert "Upstream Correlation" in message
    assert "Correlated signals" in message
    assert "Most likely causal drivers" in message
    assert "orders-web-asg" in message

    block_text = str(blocks)
    assert "Upstream Correlation" in block_text
    assert "orders-web-asg" in block_text
