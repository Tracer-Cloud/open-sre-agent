"""Allowlist filtering for import-graph edges."""

from __future__ import annotations

from tools.architecture_issue_tool.scanners.import_graph.models import LayerContract, ResolvedEdge


def _edge_label(edge: ResolvedEdge) -> str:
    return f"{edge.source_unit} -> {edge.target_unit}"


def is_allowlisted(edge: ResolvedEdge, contract: LayerContract) -> bool:
    """Return True when *edge* matches a contract allowlist entry."""
    label = _edge_label(edge)
    for entry in contract.allowlist:
        if entry == label:
            return True
        if "->" in entry:
            source, target = (part.strip() for part in entry.split("->", 1))
            if edge.source_unit == source and edge.target_unit == target:
                return True
    return False


def filter_edges(
    edges: list[ResolvedEdge],
    contract: LayerContract,
    *,
    include_baselines: bool,
) -> list[ResolvedEdge]:
    """Apply allowlist suppressions unless *include_baselines* is True."""
    if include_baselines:
        return list(edges)
    return [edge for edge in edges if not is_allowlisted(edge, contract)]
