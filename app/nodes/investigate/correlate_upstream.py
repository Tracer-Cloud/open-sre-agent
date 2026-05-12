from __future__ import annotations

from app.correlation.providers import (
    QueryBackedUpstreamEvidenceProvider,
)
from tests.synthetic.rds_postgres.correlation.investigation_flow import (
    investigate_upstream_candidates,
)


def node_correlate_upstream(state: dict) -> dict:
    raw_alert = state.get("raw_alert", {})
    service_name = raw_alert.get("service", "unknown")

    provider = QueryBackedUpstreamEvidenceProvider()

    evidence = provider.collect_upstream_evidence(
        alert_id=str(raw_alert.get("id", "synthetic-alert")),
        service_name=service_name,
        window_start="2026-04-15T14:00:00Z",
        window_end="2026-04-15T14:15:00Z",
    )

    report = investigate_upstream_candidates(
        evidence=evidence,
        rds_metric_name="orders-rds-cpu",
    )

    return {
        "correlation": {
            "correlated_signals": [signal.__dict__ for signal in report.correlated_signals],
            "most_likely_causal_drivers": [
                candidate.__dict__ for candidate in report.most_likely_causal_drivers
            ],
        }
    }
