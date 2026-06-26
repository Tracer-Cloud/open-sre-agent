"""Tests for infra/ci/check_direct_imports.py."""

from __future__ import annotations

import sys
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parents[2] / "infra" / "ci"
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_direct_imports import find_direct_violations


def test_find_direct_violations_flags_new_edge() -> None:
    graph = {
        "integrations.opensre.seed_evidence": {"tools.GrafanaLogsTool"},
        "integrations.hermes.sinks": {"tools.watch_dog.alarms"},
    }
    violations = find_direct_violations(graph, baseline_ignores=frozenset())
    edges = {v.edge for v in violations}
    assert "integrations.opensre.seed_evidence -> tools.GrafanaLogsTool" in edges
    assert "integrations.hermes.sinks -> tools.watch_dog.alarms" in edges


def test_find_direct_violations_respects_baseline() -> None:
    graph = {
        "integrations.hermes.sinks": {"tools.watch_dog.alarms"},
    }
    violations = find_direct_violations(
        graph,
        baseline_ignores=frozenset({"integrations.hermes.sinks -> tools.watch_dog.alarms"}),
    )
    assert violations == []
