from __future__ import annotations

from typing import Any

from app.core.domain.correlation.scoring import (
    TopologyNode,
    metric_to_time_series,
    rank_upstream_candidates,
    score_candidate_correlation,
    score_operator_hint,
    score_periodic_spikes,
    score_time_window_correlation,
    score_topology_adjacency,
)
from app.core.domain.types.upstream import (
    CorrelatedSignal,
    UpstreamCandidate,
    UpstreamEvidenceBundle,
)
from app.core.orchestration.node.publish_findings.upstream_correlation.reporting import (
    build_correlation_report,
    correlation_report_to_payload,
)


def _empty_correlation() -> dict[str, Any]:
    return {
        "correlated_signals": [],
        "most_likely_causal_drivers": [],
    }


def build_runtime_correlation(
    evidence: UpstreamEvidenceBundle,
    *,
    target_resource: str,
) -> dict[str, Any]:
    if not evidence.rds_metrics or not evidence.upstream_metrics:
        return _empty_correlation()

    rds_metric = evidence.rds_metrics[0]
    candidates: list[UpstreamCandidate] = []

    for metric in evidence.upstream_metrics:
        target_names = {target_resource, "rds"}

        matching_hints = tuple(
            hint
            for hint in evidence.topology_hints
            if hint.source == metric.name
            and hint.target in target_names
            and hint.relation == "upstream_of"
        )

        topology = score_topology_adjacency(
            source=TopologyNode(
                name=metric.name,
                node_type="service",
                upstream_of=tuple(hint.target for hint in matching_hints),
            ),
            target=TopologyNode(
                name=target_resource,
                node_type="rds",
                upstream_of=(),
            ),
        )

        periodicity = score_periodic_spikes(
            signal_name=metric.name,
            values=metric.values,
            spike_threshold=75.0,
        )

        operator_hint = score_operator_hint(
            metric_name=metric.name,
            operator_hints=evidence.operator_hints,
        )

        score = score_candidate_correlation(
            candidate_name=metric.name,
            time_window=score_time_window_correlation(
                metric_to_time_series(rds_metric),
                metric_to_time_series(metric),
            ),
            topology=topology,
            periodicity=periodicity,
            operator_hint=operator_hint,
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
    top_confidence = ranked[0].confidence if ranked else 0.0

    report = build_correlation_report(
        correlated_signals=(
            CorrelatedSignal(
                source="runtime",
                name="upstream-correlation",
                description="Runtime upstream correlation analysis.",
                score=top_confidence,
            ),
        ),
        ranked_candidates=ranked,
    )

    return correlation_report_to_payload(report)
