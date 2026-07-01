"""Tests for .github/ci/check_imports.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_CI_DIR = Path(__file__).resolve().parents[2] / ".github" / "ci"
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_imports import import_checks, main


def test_import_checks_runs_three_stages() -> None:
    assert len(import_checks()) == 3
    assert import_checks()[0].name.startswith("Import cycles")
    assert import_checks()[1].name.startswith("Import layers")
    assert import_checks()[2].name.startswith("Forbidden direct")


def test_main_reports_failure_when_any_stage_fails() -> None:
    with (
        patch("check_imports.check_import_cycles", return_value=0),
        patch("check_imports._run_importlinter", return_value=1),
        patch("check_imports.check_direct_imports", return_value=0),
    ):
        assert main([]) == 1


def test_main_passes_when_all_stages_pass() -> None:
    with (
        patch("check_imports.check_import_cycles", return_value=0),
        patch("check_imports._run_importlinter", return_value=0),
        patch("check_imports.check_direct_imports", return_value=0),
    ):
        assert main([]) == 0
