"""Tests for architecture hotspot aggregation."""

from __future__ import annotations

from tools.architecture_issue_tool.hotspots import build_hotspots
from tools.architecture_issue_tool.models import ArchitectureViolation


def _violation(
    *,
    vid: str,
    kind: str = "layer_import",
    severity: str = "p0",
    evidence: dict,
) -> ArchitectureViolation:
    return ArchitectureViolation(
        id=vid,
        kind=kind,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        title=vid,
        evidence=evidence,
        fix_direction="fix",
    )


def test_build_hotspots_ranks_by_source_module() -> None:
    violations = [
        _violation(vid="a1", evidence={"source_module": "connect", "source_file": "a.java"}),
        _violation(
            vid="a2",
            severity="p1",
            evidence={"source_module": "connect", "source_file": "b.java"},
        ),
        _violation(vid="b1", evidence={"source_module": "core", "source_file": "c.java"}),
    ]

    hotspots = build_hotspots(violations)

    assert [item["area"] for item in hotspots] == ["connect", "core"]
    assert hotspots[0]["count"] == 2
    assert hotspots[0]["share"] == 0.6667
    assert hotspots[0]["severity_counts"] == {"p0": 1, "p1": 1, "p2": 0}
    assert hotspots[0]["kind_counts"] == {"layer_import": 2}
    assert hotspots[1]["count"] == 1


def test_build_hotspots_falls_back_to_path_prefix() -> None:
    violations = [
        _violation(
            vid="o1",
            kind="compatibility_shim",
            severity="p2",
            evidence={"path": "core/big.py"},
        ),
    ]

    hotspots = build_hotspots(violations)

    assert hotspots == [
        {
            "area": "core",
            "count": 1,
            "share": 1.0,
            "severity_counts": {"p0": 0, "p1": 0, "p2": 1},
            "kind_counts": {"compatibility_shim": 1},
        }
    ]


def test_build_hotspots_empty() -> None:
    assert build_hotspots([]) == []
