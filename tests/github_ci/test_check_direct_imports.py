"""Tests for .github/ci/check_direct_imports.py."""

from __future__ import annotations

import sys
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parents[2] / ".github" / "ci"
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_direct_imports import find_direct_violations


def test_find_direct_violations_flags_new_edge() -> None:
    # All edges below are forbidden by ``_FORBIDDEN_DIRECT``:
    # - ``integrations`` cannot import from ``tools``
    # - ``platform`` cannot import from ``surfaces``
    # - ``tools`` cannot import from ``surfaces``
    graph = {
        "integrations.grafana.tools": {"tools.tool_decorator"},
        "tools.fleet_monitoring": {"surfaces.cli.commands.doctor"},
        "platform.analytics.provider": {"surfaces.cli.wizard.store"},
    }
    violations = find_direct_violations(graph, baseline_ignores=frozenset())
    edges = {v.edge for v in violations}
    assert "integrations.grafana.tools -> tools.tool_decorator" in edges
    assert "tools.fleet_monitoring -> surfaces.cli.commands.doctor" in edges
    assert "platform.analytics.provider -> surfaces.cli.wizard.store" in edges


def test_find_direct_violations_respects_baseline() -> None:
    graph = {
        "tools.fleet_monitoring": {"surfaces.cli.commands.doctor"},
    }
    violations = find_direct_violations(
        graph,
        baseline_ignores=frozenset({"tools.fleet_monitoring -> surfaces.cli.commands.doctor"}),
    )
    assert violations == []
