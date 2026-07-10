"""Tests for import-graph allowlist behavior."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.scanners.import_graph.allowlist import filter_edges
from tools.architecture_issue_tool.scanners.import_graph.graph import build_import_graph
from tools.architecture_issue_tool.scanners.import_graph.models import (
    ForbiddenDirectRule,
    LayerContract,
    ResolvedEdge,
)

_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "architecture_audit" / "polyglot_repo"
)


def test_filter_edges_honors_include_baselines_flag() -> None:
    graph, _raw_count, _resolved_count = build_import_graph(_FIXTURE_ROOT)
    contract = LayerContract(
        name="test",
        roots=("src",),
        layers=(("infra",), ("app",)),
        forbidden_direct=(ForbiddenDirectRule(source="infra", targets=("app",)),),
        allowlist=("infra -> app",),
    )
    direct_edges = [
        edge for edge in graph.edges if edge.source_unit == "infra" and edge.target_unit == "app"
    ]

    suppressed = filter_edges(direct_edges, contract, include_baselines=False)
    included = filter_edges(direct_edges, contract, include_baselines=True)

    assert suppressed == []
    assert len(included) == len(direct_edges)


def test_allowlist_entry_matches_source_and_target_units() -> None:
    edge = ResolvedEdge(
        source_unit="infra",
        target_unit="app",
        source_file="src/infra/logger.ts",
        line=1,
        import_spec="../app/main",
        language="typescript",
    )
    contract = LayerContract(
        name="test",
        roots=("src",),
        layers=(("infra",), ("app",)),
        allowlist=("infra -> app",),
    )

    filtered = filter_edges([edge], contract, include_baselines=False)

    assert filtered == []
