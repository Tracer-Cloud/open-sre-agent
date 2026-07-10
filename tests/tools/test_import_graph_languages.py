"""Tests for polyglot import extraction."""

from __future__ import annotations

from pathlib import Path

from tools.architecture_issue_tool.scanners.import_graph.languages.extract import (
    extract_raw_imports,
)

_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "architecture_audit" / "polyglot_repo"
)


def test_extract_raw_imports_across_languages() -> None:
    imports = extract_raw_imports(_FIXTURE_ROOT)
    specs = {(item.language, item.import_spec) for item in imports}

    assert ("go", "../app/handler") in specs
    assert ("typescript", "../app/main") in specs
    assert ("python", "app.bootstrap") in specs
