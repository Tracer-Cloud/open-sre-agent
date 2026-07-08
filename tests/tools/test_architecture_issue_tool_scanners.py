"""Tests for architecture issue tool scanners."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools.architecture_issue_tool.scanners.compatibility_shims import scan_compatibility_shims
from tools.architecture_issue_tool.scanners.import_checks import (
    parse_direct_imports_output,
    parse_lint_imports_output,
    scan_import_violations,
)
from tools.architecture_issue_tool.scanners.module_placement import scan_module_placement
from tools.architecture_issue_tool.scanners.oversized_files import scan_oversized_files

_LINT_IMPORTS_FIXTURE = """
Broken contracts
----------------

OpenSRE package layers (transitive)
-----------------------------------

core is not allowed to import integrations:

- core.llm.factory -> integrations.llm_cli.registry (l.92)

tools is not allowed to import surfaces:

- tools.interactive_shell.actions.slash -> surfaces.interactive_shell.ui (l.20)
"""


def test_parse_lint_imports_output_extracts_edges() -> None:
    edges = parse_lint_imports_output(_LINT_IMPORTS_FIXTURE)

    assert len(edges) == 2
    assert edges[0].source_module == "core.llm.factory"
    assert edges[0].target_module == "integrations.llm_cli.registry"
    assert edges[0].line == 92
    assert "core is not allowed to import integrations" in edges[0].rule


def test_parse_direct_imports_output_extracts_edges() -> None:
    stdout = (
        "FAIL: 1 forbidden module-level direct import edge(s):\n"
        "  core.agent.foo -> surfaces.interactive_shell.ui\n\n"
        "FAIL: 1 forbidden nested direct import edge(s):\n"
        "  tools.interactive_shell.actions.slash -> surfaces.interactive_shell.ui (line 16)\n"
    )

    edges = parse_direct_imports_output(stdout)

    assert len(edges) == 2
    assert edges[1].line == 16


def test_scan_oversized_files_flags_large_python_file(tmp_path: Path) -> None:
    target = tmp_path / "core"
    target.mkdir()
    large_file = target / "big.py"
    large_file.write_text("x = 1\n" * 501, encoding="utf-8")

    violations = scan_oversized_files(tmp_path, max_lines=500)

    assert len(violations) == 1
    assert violations[0].kind == "oversized_file"
    assert violations[0].evidence["path"] == "core/big.py"


def test_scan_compatibility_shims_detects_reexport_init(tmp_path: Path) -> None:
    package = tmp_path / "integrations" / "demo"
    package.mkdir(parents=True)
    init_file = package / "__init__.py"
    init_file.write_text("from integrations.demo.client import DemoClient\n", encoding="utf-8")

    violations = scan_compatibility_shims(tmp_path)

    assert any(v.evidence.get("pattern") == "reexport_only_init" for v in violations)


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


@patch("tools.architecture_issue_tool.scanners.import_checks._scan_direct_imports")
@patch("tools.architecture_issue_tool.scanners.import_checks._scan_layer_imports")
def test_scan_import_violations_combines_results(
    mock_layer,
    mock_direct,
) -> None:
    from tools.architecture_issue_tool.models import ArchitectureViolation

    mock_layer.return_value = (
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
        ["layer warning"],
    )
    mock_direct.return_value = (
        [
            ArchitectureViolation(
                id="v-2",
                kind="direct_import",
                severity="p1",
                title="direct",
                evidence={"edge": "c -> d"},
                fix_direction="fix",
            )
        ],
        [],
    )

    violations, warnings = scan_import_violations(Path("/tmp/repo"))

    assert len(violations) == 2
    assert warnings == ["layer warning"]
