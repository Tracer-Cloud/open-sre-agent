from __future__ import annotations

from app.correlation.node import node_correlate_upstream
from app.correlation.upstream import MetricSeries, UpstreamEvidenceBundle


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def collect_upstream_evidence(
        self,
        *,
        alert_id: str,
        service_name: str,
        window_start: str,
        window_end: str,
    ) -> UpstreamEvidenceBundle:
        self.calls.append(
            {
                "alert_id": alert_id,
                "service_name": service_name,
                "window_start": window_start,
                "window_end": window_end,
            }
        )
        return UpstreamEvidenceBundle(
            rds_metrics=(
                MetricSeries(
                    source="datadog",
                    name="aws.rds.cpuutilization",
                    timestamps=("2026-04-15T14:00:00Z",),
                    values=(90.0,),
                ),
            ),
            upstream_metrics=(
                MetricSeries(
                    source="datadog",
                    name="orders-web-cpu",
                    timestamps=("2026-04-15T14:00:00Z",),
                    values=(85.0,),
                ),
            ),
        )


def test_correlate_upstream_invokes_configured_provider() -> None:
    provider = RecordingProvider()

    result = node_correlate_upstream(
        {
            "raw_alert": {
                "alert_id": "alert-1",
                "service": "orders",
                "resource": "orders-prod-mysql",
            },
            "incident_window": {
                "since": "2026-04-15T14:00:00Z",
                "until": "2026-04-15T14:15:00Z",
            },
        },
        {
            "configurable": {
                "upstream_evidence_provider": provider,
            }
        },
    )

    assert provider.calls == [
        {
            "alert_id": "alert-1",
            "service_name": "orders",
            "window_start": "2026-04-15T14:00:00Z",
            "window_end": "2026-04-15T14:15:00Z",
        }
    ]
    correlation = result["correlation"]

    assert correlation["correlated_signals"]
    assert correlation["most_likely_causal_drivers"]

    driver = correlation["most_likely_causal_drivers"][0]
    assert driver["name"] == "orders-web-cpu"
