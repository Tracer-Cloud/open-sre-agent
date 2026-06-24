from __future__ import annotations

from typing import Any, cast

from app.core.orchestration.node.publish_findings.upstream_correlation import (
    node as correlation_node,
)
from app.core.orchestration.node.publish_findings.upstream_correlation.registry import (
    build_upstream_evidence_provider,
)
from app.state import InvestigationState


def build_correlation_config(state: InvestigationState | dict[str, Any]) -> dict[str, Any] | None:
    """Return the runtime config carrying the upstream-evidence provider."""
    provider = build_upstream_evidence_provider(cast(dict[str, Any], state))
    if provider is None:
        return None
    return {"configurable": {"upstream_evidence_provider": provider}}


def enrich_upstream_correlation(state: InvestigationState) -> dict[str, Any]:
    """Build upstream-correlation state updates for report generation."""
    config = build_correlation_config(state)
    return correlation_node.node_correlate_upstream(state, config)
