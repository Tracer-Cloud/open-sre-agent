from __future__ import annotations

from app.core.domain.types.upstream import (
    LogSignal,
    MetricSeries,
    TopologyHint,
    UpstreamEvidenceBundle,
    UpstreamEvidenceProvider,
)
from app.core.orchestration.node.publish_findings.upstream_correlation.enrich import (
    build_correlation_config,
    enrich_upstream_correlation,
)
from app.core.orchestration.node.publish_findings.upstream_correlation.providers import (
    NoopUpstreamEvidenceProvider,
    QueryBackedUpstreamEvidenceProvider,
)
from app.core.orchestration.node.publish_findings.upstream_correlation.registry import (
    build_upstream_evidence_provider,
    candidate_services_from_state,
    target_resource_from_state,
)

__all__ = [
    "LogSignal",
    "MetricSeries",
    "NoopUpstreamEvidenceProvider",
    "QueryBackedUpstreamEvidenceProvider",
    "TopologyHint",
    "UpstreamEvidenceBundle",
    "UpstreamEvidenceProvider",
    "build_correlation_config",
    "build_upstream_evidence_provider",
    "candidate_services_from_state",
    "enrich_upstream_correlation",
    "target_resource_from_state",
]
