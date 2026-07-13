"""Tests for architecture issue tool scanners."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools.architecture_issue_tool.scanners.import_checks import scan_import_violations
from tools.architecture_issue_tool.scanners.module_placement import scan_module_placement

_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "architecture_audit" / "polyglot_repo"
)


def test_scan_import_violations_uses_tree_sitter_graph() -> None:
    violations, warnings = scan_import_violations(_FIXTURE_ROOT)

    assert any(violation.kind == "layer_import" for violation in violations)
    assert any(violation.kind == "direct_import" for violation in violations)
    assert warnings == []


def test_scan_module_placement_flags_known_vendor_tool(tmp_path: Path) -> None:
    package = tmp_path / "tools" / "community_followup_tool"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# placeholder\n", encoding="utf-8")

    violations = scan_module_placement(tmp_path)

    assert any(v.evidence.get("pattern") == "known_vendor_tool" for v in violations)


def test_scan_module_placement_flags_legacy_vendors_import(tmp_path: Path) -> None:
    package = tmp_path / "core"
    package.mkdir()
    (package / "legacy.py").write_text("from vendors.old import thing\n", encoding="utf-8")

    violations = scan_module_placement(tmp_path)

    assert any(v.severity == "p0" for v in violations)
    assert any(v.evidence.get("package") == "vendors" for v in violations)


@patch("tools.architecture_issue_tool.scanners.import_checks.scan_import_graph")
def test_scan_import_violations_delegates_to_import_graph(mock_scan) -> None:
    from tools.architecture_issue_tool.models import ArchitectureViolation

    mock_scan.return_value = (
        [
            ArchitectureViolation(
                id="v-1",
                kind="layer_import",
                severity="p0",
                title="layer",
                evidence={"edge": "a -> b"},
                fix_direction="fix",
            )
        ],
        ["warning"],
    )

    violations, warnings = scan_import_violations(Path("/tmp/repo"), strict_layers=False)

    mock_scan.assert_called_once_with(
        Path("/tmp/repo"),
        strict_layers=False,
        include_baselines=False,
    )
    assert len(violations) == 1
    assert warnings == ["warning"]
