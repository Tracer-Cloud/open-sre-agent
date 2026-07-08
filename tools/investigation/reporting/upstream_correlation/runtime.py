from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.domain.correlation.scoring import (
    rank_upstream_candidates,
    score_candidate_correlation,
    score_periodic_spikes,
    score_time_window_correlation,
    score_topology_adjacency,
)
from core.domain.types.upstream import (
    CorrelatedSignal,
    TopologyNode,
    UpstreamCandidate,
    UpstreamEvidenceBundle,
    metric_to_time_series,
)
from tools.investigation.reporting.upstream_correlation.feature_config import (
    load_feature_workflow_config,
    resolve_feature_keywords,
)
from tools.investigation.reporting.upstream_correlation.feature_workflow import (
    score_feature_workflow_hypothesis,
)
from tools.investigation.reporting.upstream_correlation.reporting import (
    build_correlation_report,
    correlation_report_to_payload,
)

_FEATURE_CONFIG_ENV = "OPENSRE_FEATURE_WORKFLOW_CONFIG"


def _runtime_feature_keywords(
    *,
    endpoint: str | None,
    service_name: str,
) -> tuple[str, ...]:
    config_path = os.getenv(_FEATURE_CONFIG_ENV)
    if not config_path:
        return ()

    config_file = Path(config_path)
    if not config_file.exists():
        return ()

    try:
        config = load_feature_workflow_config(config_file)
    except Exception:
        return ()

    return resolve_feature_keywords(
        endpoint=endpoint,
        service_name=service_name,
        config=config,
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
    endpoint = next((hint for hint in evidence.operator_hints if hint.startswith("/")), None)
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

        metric_keywords = tuple(
            token
            for token in metric.name.lower()
            .replace("{", " ")
            .replace("}", " ")
            .replace(":", " ")
            .replace(",", " ")
            .replace(".", " ")
            .replace("-", " ")
            .split()
            if len(token) > 2
        )

        config_keywords = _runtime_feature_keywords(
            endpoint=endpoint,
            service_name=metric.name,
        )

        candidate_keywords = tuple(dict.fromkeys(metric_keywords + config_keywords))

        workflow_hints = tuple(hint for hint in evidence.operator_hints if not hint.startswith("/"))

        feature_workflow = score_feature_workflow_hypothesis(
            candidate_name=metric.name,
            candidate_keywords=candidate_keywords,
            operator_hints=workflow_hints,
        )

        score = score_candidate_correlation(
            candidate_name=metric.name,
            time_window=score_time_window_correlation(
                metric_to_time_series(rds_metric),
                metric_to_time_series(metric),
            ),
            topology=topology,
            periodicity=periodicity,
            operator_hint=feature_workflow,
        )

        candidates.append(
            UpstreamCandidate(
                name=metric.name,
                tier="application",
                confidence=score.final_confidence,
                confidence_label=score.weighted_confidence.label,
                correlated_signals=(),
                rationale=score.rationale,
                evidence_breakdown=tuple(
                    {
                        "source": contribution.source,
                        "score": contribution.score,
                        "weight": contribution.weight,
                        "rationale": contribution.rationale,
                    }
                    for contribution in score.weighted_confidence.contributions
                ),
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
