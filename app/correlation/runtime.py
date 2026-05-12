from __future__ import annotations

from app.correlation.upstream import MetricSeries, UpstreamEvidenceBundle
from tests.synthetic.rds_postgres.correlation.candidate_scoring import (
    score_candidate_correlation,
)
from tests.synthetic.rds_postgres.correlation.models import (
    CorrelatedSignal,
    UpstreamCandidate,
)
from tests.synthetic.rds_postgres.correlation.periodicity import (
    PeriodicityScore,
)
from tests.synthetic.rds_postgres.correlation.ranking import (
    rank_upstream_candidates,
)
from tests.synthetic.rds_postgres.correlation.reporting import (
    build_correlation_report,
    correlation_report_to_payload,
)
from tests.synthetic.rds_postgres.correlation.time_window import (
    TimeSeries,
    score_time_window_correlation,
)
from tests.synthetic.rds_postgres.correlation.topology import (
    TopologyNode,
    score_topology_adjacency,
)


def _to_time_series(metric: MetricSeries) -> TimeSeries:
    return TimeSeries(
        name=metric.name,
        timestamps=metric.timestamps,
        values=metric.values,
    )


def build_runtime_correlation(
    evidence: UpstreamEvidenceBundle,
) -> dict[str, object]:
    if not evidence.rds_metrics or not evidence.upstream_metrics:
        return {
            "correlated_signals": [],
            "most_likely_causal_drivers": [],
        }

    rds_metric = evidence.rds_metrics[0]

    candidates: list[UpstreamCandidate] = []

    for metric in evidence.upstream_metrics:
        score = score_candidate_correlation(
            candidate_name=metric.name,
            time_window=score_time_window_correlation(
                _to_time_series(rds_metric),
                _to_time_series(metric),
            ),
            topology=score_topology_adjacency(
                source=TopologyNode(
                    name=metric.name,
                    node_type="service",
                    upstream_of=("orders-prod-mysql",),
                ),
                target=TopologyNode(
                    name="orders-prod-mysql",
                    node_type="rds_mysql",
                    upstream_of=(),
                ),
            ),
            periodicity=PeriodicityScore(
                signal_name=metric.name,
                repeated_spikes=2,
                score=0.5,
                rationale="Repeated upstream load pattern detected.",
            ),
        )

        candidates.append(
            UpstreamCandidate(
                name=metric.name,
                tier="application",
                confidence=score.final_confidence,
                correlated_signals=(),
                rationale=score.rationale,
            )
        )

    ranked = rank_upstream_candidates(candidates)

    report = build_correlation_report(
        correlated_signals=(
            CorrelatedSignal(
                source="runtime",
                name="upstream-correlation",
                description="Runtime upstream correlation analysis.",
                score=1.0,
            ),
        ),
        ranked_candidates=ranked,
    )

    return correlation_report_to_payload(report)
