"""Forbidden direct import evaluation."""

from __future__ import annotations

from tools.architecture_issue_tool.scanners.import_graph.models import (
    ImportGraph,
    LayerContract,
    ResolvedEdge,
)


def find_direct_violations(graph: ImportGraph, contract: LayerContract) -> list[ResolvedEdge]:
    """Return direct edges that match forbidden cross-unit rules."""
    violations: list[ResolvedEdge] = []
    for edge in graph.edges:
        for rule in contract.forbidden_direct:
            if edge.source_unit != rule.source:
                continue
            if edge.target_unit in rule.targets:
                violations.append(edge)
                break
    return violations
