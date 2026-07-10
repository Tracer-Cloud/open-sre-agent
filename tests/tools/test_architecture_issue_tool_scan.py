"""Tests for architecture scan orchestration and scan_summary metadata."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools.architecture_issue_tool.models import ArchitectureViolation
from tools.architecture_issue_tool.repo_workspace import RepoWorkspace
from tools.architecture_issue_tool.scan import run_architecture_scan

_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "architecture_audit" / "polyglot_repo"
)


def test_run_architecture_scan_marks_skipped_import_categories(tmp_path: Path) -> None:
    workspace = RepoWorkspace(owner="org", repo="repo", ref="main", root=tmp_path)

    with patch(
        "tools.architecture_issue_tool.scan.scan_import_violations",
        return_value=([], ["no supported source files found in cloned repository"]),
    ):
        result = run_architecture_scan(
            workspace,
            categories=["layer_import", "direct_import"],
        )

    summary = result["scan_summary"]
    assert summary["categories_skipped"] == ["layer_import", "direct_import"]
    assert summary["coverage_complete"] is False
    assert "no supported source files found" in summary["warnings"][0]


def test_run_architecture_scan_populates_severity_and_kind_counts(tmp_path: Path) -> None:
    workspace = RepoWorkspace(owner="org", repo="repo", ref="main", root=tmp_path)
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "big.py").write_text("x = 1\n" * 501, encoding="utf-8")

    with patch(
        "tools.architecture_issue_tool.scan.scan_import_violations",
        return_value=([], []),
    ):
        result = run_architecture_scan(
            workspace,
            categories=["oversized_file"],
        )

    summary = result["scan_summary"]
    assert summary["severity_counts"] == {"p0": 0, "p1": 0, "p2": 1}
    assert summary["kind_counts"] == {"oversized_file": 1}
    assert summary["coverage_complete"] is True
    assert summary["categories_skipped"] == []


def test_run_architecture_scan_includes_import_violation_counts(tmp_path: Path) -> None:
    workspace = RepoWorkspace(owner="org", repo="repo", ref="main", root=tmp_path)

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
        result = run_architecture_scan(workspace, categories=["layer_import", "direct_import"])

    summary = result["scan_summary"]
    assert summary["severity_counts"] == {"p0": 1, "p1": 1, "p2": 0}
    assert summary["kind_counts"] == {"layer_import": 1, "direct_import": 1}
    assert summary["categories_skipped"] == []
    assert summary["coverage_complete"] is True


def test_run_architecture_scan_on_polyglot_fixture() -> None:
    workspace = RepoWorkspace(
        owner="fixture",
        repo="polyglot",
        ref="main",
        root=_FIXTURE_ROOT,
    )

    result = run_architecture_scan(workspace, categories=["layer_import", "direct_import"])

    summary = result["scan_summary"]
    assert summary["categories_skipped"] == []
    assert summary["coverage_complete"] is True
    assert summary["kind_counts"].get("layer_import", 0) > 0
    assert summary["kind_counts"].get("direct_import", 0) > 0
