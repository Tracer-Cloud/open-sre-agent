"""Tests for import-graph layer evaluation."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.scanners.import_graph.graph import build_import_graph
from tools.architecture_issue_tool.scanners.import_graph.layers import find_layer_violations
from tools.architecture_issue_tool.scanners.import_graph.scan import scan_import_graph

_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "architecture_audit" / "polyglot_repo"
)


def test_find_layer_violations_on_polyglot_fixture() -> None:
    from tools.architecture_issue_tool.scanners.import_graph.contracts.resolve import (
        resolve_contract,
    )

    graph, _raw_count, _resolved_count = build_import_graph(_FIXTURE_ROOT)
    contract = resolve_contract(_FIXTURE_ROOT)
    violations = find_layer_violations(graph, contract, strict_layers=False)

    assert any(edge.source_unit == "infra" and edge.target_unit == "app" for edge in violations)


def test_scan_import_graph_reports_layer_and_direct_violations() -> None:
    violations, warnings = scan_import_graph(_FIXTURE_ROOT)

    kinds = {violation.kind for violation in violations}
    assert "layer_import" in kinds
    assert "direct_import" in kinds
    assert any(violation.evidence["source_module"] == "infra" for violation in violations)
    assert warnings == []
