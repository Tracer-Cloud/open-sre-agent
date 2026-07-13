"""Tests for architecture import/placement scan helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools.architecture_issue_tool.models import ArchitectureViolation
from tools.architecture_issue_tool.scan import scan_imports_at_path, scan_placement_at_path

_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "architecture_audit" / "polyglot_repo"
)


def test_scan_imports_marks_skipped_categories(tmp_path: Path) -> None:
    with patch(
        "tools.architecture_issue_tool.scan.scan_import_violations",
        return_value=([], ["no supported source files found in cloned repository"]),
    ):
        result = scan_imports_at_path(tmp_path, owner="org", repo="repo")

    summary = result["scan_summary"]
    assert summary["categories_skipped"] == ["layer_import", "direct_import"]
    assert summary["coverage_complete"] is False
    assert "no supported source files found" in summary["warnings"][0]


def test_scan_imports_includes_violation_counts(tmp_path: Path) -> None:
    layer_violation = ArchitectureViolation(
        id="v-layer",
        kind="layer_import",
        severity="p0",
        title="layer",
        evidence={"edge": "core.a -> integrations.b"},
        fix_direction="fix",
    )
    direct_violation = ArchitectureViolation(
        id="v-direct",
        kind="direct_import",
        severity="p1",
        title="direct",
        evidence={"edge": "tools.a -> surfaces.b"},
        fix_direction="fix",
    )

    with patch(
        "tools.architecture_issue_tool.scan.scan_import_violations",
        return_value=([layer_violation, direct_violation], []),
    ):
        result = scan_imports_at_path(tmp_path, owner="org", repo="repo")

    summary = result["scan_summary"]
    assert summary["severity_counts"] == {"p0": 1, "p1": 1, "p2": 0}
    assert summary["kind_counts"] == {"layer_import": 1, "direct_import": 1}
    assert summary["categories_skipped"] == []
    assert summary["coverage_complete"] is True


def test_scan_imports_on_polyglot_fixture() -> None:
    result = scan_imports_at_path(_FIXTURE_ROOT, owner="fixture", repo="polyglot")

    summary = result["scan_summary"]
    assert summary["categories_skipped"] == []
    assert summary["coverage_complete"] is True
    assert summary["kind_counts"].get("layer_import", 0) > 0
    assert summary["kind_counts"].get("direct_import", 0) > 0


def test_scan_placement_runs_without_tools_or_integrations(tmp_path: Path) -> None:
    """Placement coverage stays complete on non-OpenSRE layouts."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = scan_placement_at_path(tmp_path, owner="org", repo="repo")

    summary = result["scan_summary"]
    assert summary["categories_skipped"] == []
    assert summary["coverage_complete"] is True
    assert summary["violations"] == 0
    assert summary["warnings"] == []


def test_scan_placement_detects_legacy_imports_without_opensre_dirs(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "legacy.py").write_text("from vendors.foo import bar\n", encoding="utf-8")

    result = scan_placement_at_path(tmp_path, owner="org", repo="repo")

    summary = result["scan_summary"]
    assert summary["coverage_complete"] is True
    assert summary["kind_counts"].get("misplaced_module", 0) == 1
    assert result["violations"][0]["evidence"]["package"] == "vendors"
