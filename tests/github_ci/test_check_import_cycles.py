"""Tests for .github/ci/check_import_cycles.py."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_DIR = _REPO_ROOT / ".github" / "ci"
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_import_cycles import discover_first_party_roots, main


def test_discover_first_party_roots_includes_known_packages() -> None:
    # parents[2] = repo root (tests/github_ci/<file>.py); parents[1] would
    # be the tests/ directory, which happens to contain similarly-named
    # subdirectories and would silently produce a passing assertion.
    roots = discover_first_party_roots(_REPO_ROOT)
    assert "surfaces" in roots
    assert "core" in roots
    assert "integrations" in roots
    assert "tools" in roots


def test_discover_first_party_roots_excludes_tests() -> None:
    roots = discover_first_party_roots(_REPO_ROOT)
    assert "tests" not in roots
    assert "infra" not in roots


def test_main_reports_no_cycles_on_clean_tree() -> None:
    assert main([]) == 0
