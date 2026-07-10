"""Import-graph scan entrypoint."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tools.architecture_issue_tool.models import ArchitectureViolation, Severity, ViolationKind
from tools.architecture_issue_tool.scanners._paths import iter_source_files
from tools.architecture_issue_tool.scanners.import_graph.allowlist import filter_edges
from tools.architecture_issue_tool.scanners.import_graph.contracts.resolve import resolve_contract
from tools.architecture_issue_tool.scanners.import_graph.direct import find_direct_violations
from tools.architecture_issue_tool.scanners.import_graph.graph import build_import_graph
from tools.architecture_issue_tool.scanners.import_graph.layers import find_layer_violations
from tools.architecture_issue_tool.scanners.import_graph.models import ResolvedEdge

_P0_SOURCE_MARKERS = frozenset({"core", "config", "integrations", "infra", "internal", "platform"})


def _violation_id(
    kind: str, source_unit: str, target_unit: str, source_file: str, line: int
) -> str:
    digest = hashlib.sha256(
        f"{kind}:{source_unit}->{target_unit}:{source_file}:{line}".encode()
    ).hexdigest()
    return f"{kind[:1]}-{digest[:12]}"


def _severity_for_edge(edge: ResolvedEdge) -> Severity:
    if edge.source_unit.lower() in _P0_SOURCE_MARKERS:
        return "p0"
    return "p1"


def _edge_to_violation(
    edge: ResolvedEdge,
    *,
    kind: ViolationKind,
    title: str,
) -> ArchitectureViolation:
    edge_label = f"{edge.source_unit} -> {edge.target_unit}"
    return ArchitectureViolation(
        id=_violation_id(kind, edge.source_unit, edge.target_unit, edge.source_file, edge.line),
        kind=kind,
        severity=_severity_for_edge(edge),
        title=title,
        evidence={
            "source_module": edge.source_unit,
            "target_module": edge.target_unit,
            "source_file": edge.source_file,
            "line": edge.line,
            "import_spec": edge.import_spec,
            "language": edge.language,
            "edge": edge_label,
        },
        fix_direction=(
            f"Remove or refactor the import edge {edge_label} in {edge.source_file} "
            f"so it respects the repository layer contract."
        ),
    )


_LOW_EDGE_RESOLUTION_RATIO = 0.01


def _import_resolution_warning(raw_import_count: int, resolved_edge_count: int) -> str | None:
    if raw_import_count == 0:
        return None
    ratio = resolved_edge_count / raw_import_count
    if ratio >= _LOW_EDGE_RESOLUTION_RATIO:
        return None
    percent = round(ratio * 100, 2)
    return (
        f"import graph resolved only {resolved_edge_count} of {raw_import_count} extracted imports "
        f"({percent}%); layer/direct results may under-report for this repository layout"
    )


def scan_import_graph(
    clone_root: Path,
    *,
    strict_layers: bool = True,
    include_baselines: bool = False,
) -> tuple[list[ArchitectureViolation], list[str]]:
    """Scan *clone_root* for layer and direct import violations via tree-sitter."""
    warnings: list[str] = []
    source_files = list(iter_source_files(clone_root))
    if not source_files:
        return [], ["no supported source files found in cloned repository"]

    try:
        graph, raw_import_count, resolved_edge_count = build_import_graph(clone_root)
    except Exception as exc:  # noqa: BLE001 - surface parse failures as warnings
        return [], [f"import graph build failed: {exc}"]

    resolution_warning = _import_resolution_warning(raw_import_count, resolved_edge_count)
    if resolution_warning is not None:
        warnings.append(resolution_warning)

    if not graph.edges:
        if not warnings:
            warnings.append("no resolvable cross-unit import edges found")
        return [], warnings

    contract = resolve_contract(clone_root)
    layer_edges = find_layer_violations(graph, contract, strict_layers=strict_layers)
    direct_edges = find_direct_violations(graph, contract)
    layer_edges = filter_edges(layer_edges, contract, include_baselines=include_baselines)
    direct_edges = filter_edges(direct_edges, contract, include_baselines=include_baselines)

    violations: list[ArchitectureViolation] = []
    for edge in layer_edges:
        violations.append(
            _edge_to_violation(
                edge,
                kind="layer_import",
                title=(
                    f"{edge.source_unit} is not allowed to import {edge.target_unit} "
                    f"(contract: {contract.name})"
                ),
            )
        )
    for edge in direct_edges:
        violations.append(
            _edge_to_violation(
                edge,
                kind="direct_import",
                title=(
                    f"Forbidden direct import from {edge.source_unit} to {edge.target_unit} "
                    f"(contract: {contract.name})"
                ),
            )
        )
    return violations, warnings
