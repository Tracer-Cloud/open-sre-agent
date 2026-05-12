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
    CorrelationReport,
    build_correlation_report,
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


def investigate_upstream_candidates(
    *,
    evidence: UpstreamEvidenceBundle,
) -> CorrelationReport:
    rds_metric = evidence.rds_metrics[0]

    candidates: list[UpstreamCandidate] = []

    for metric in evidence.upstream_metrics:
        source_node = TopologyNode(
            name=metric.name,
            node_type="service",
            upstream_of=("orders-prod-mysql",),
        )

        target_node = TopologyNode(
            name="orders-prod-mysql",
            node_type="rds_mysql",
            upstream_of=(),
        )

        score = score_candidate_correlation(
            candidate_name=metric.name,
            time_window=score_time_window_correlation(
                _to_time_series(rds_metric),
                _to_time_series(metric),
            ),
            topology=score_topology_adjacency(
                source=source_node,
                target=target_node,
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

    return build_correlation_report(
        correlated_signals=(
            CorrelatedSignal(
                source="aws_cloudwatch_metrics",
                name="CPUUtilization",
                description="RDS CPU spike correlated with upstream tier load.",
                score=1.0,
            ),
        ),
        ranked_candidates=ranked,
    )
