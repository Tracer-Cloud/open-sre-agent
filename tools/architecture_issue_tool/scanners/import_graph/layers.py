"""Layer violation evaluation for import graphs."""

from __future__ import annotations

from collections import deque

from tools.architecture_issue_tool.scanners.import_graph.models import (
    ImportGraph,
    LayerContract,
    ResolvedEdge,
)


def _unit_layer_index(contract: LayerContract, unit: str) -> int | None:
    for index, layer in enumerate(contract.layers):
        if unit in layer:
            return index
    return None


def _is_upward_import(contract: LayerContract, source_unit: str, target_unit: str) -> bool:
    source_layer = _unit_layer_index(contract, source_unit)
    target_layer = _unit_layer_index(contract, target_unit)
    if source_layer is None or target_layer is None:
        return False
    return source_layer < target_layer


def _reachable_targets(graph: ImportGraph, start: str) -> set[str]:
    visited: set[str] = set()
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for target in graph.adjacency.get(current, set()):
            if target in visited:
                continue
            visited.add(target)
            queue.append(target)
    return visited


def find_layer_violations(
    graph: ImportGraph,
    contract: LayerContract,
    *,
    strict_layers: bool,
) -> list[ResolvedEdge]:
    """Return edges that violate layer ordering."""
    if strict_layers:
        violations: list[ResolvedEdge] = []
        seen: set[tuple[str, str, str, int]] = set()
        for edge in graph.edges:
            if not _is_upward_import(contract, edge.source_unit, edge.target_unit):
                continue
            key = (edge.source_unit, edge.target_unit, edge.source_file, edge.line)
            if key not in seen:
                seen.add(key)
                violations.append(edge)

        for source_unit in sorted(graph.units):
            source_layer = _unit_layer_index(contract, source_unit)
            if source_layer is None:
                continue
            reachable = _reachable_targets(graph, source_unit)
            for target_unit in sorted(reachable):
                target_layer = _unit_layer_index(contract, target_unit)
                if target_layer is None or source_layer >= target_layer:
                    continue
                for edge in graph.edges:
                    if edge.source_unit != source_unit or edge.target_unit != target_unit:
                        continue
                    if edge in violations:
                        continue
                    key = (edge.source_unit, edge.target_unit, edge.source_file, edge.line)
                    if key not in seen:
                        seen.add(key)
                        violations.append(edge)
        return violations

    return [
        edge
        for edge in graph.edges
        if _is_upward_import(contract, edge.source_unit, edge.target_unit)
    ]
