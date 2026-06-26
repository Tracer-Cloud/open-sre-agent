"""Tests for scripts/check_import_cycles.py."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_import_cycles import discover_first_party_roots, main


def test_discover_first_party_roots_includes_known_packages() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    roots = discover_first_party_roots(repo_root)
    assert "cli" in roots
    assert "core" in roots
    assert "integrations" in roots
    assert "tools" in roots


def test_discover_first_party_roots_excludes_tests_and_scripts() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    roots = discover_first_party_roots(repo_root)
    assert "tests" not in roots
    assert "scripts" not in roots


def test_main_reports_no_cycles_on_clean_tree() -> None:
    assert main([]) == 0
