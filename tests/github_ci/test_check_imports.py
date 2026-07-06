"""Tests for .github/ci/check_imports.py."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_CI_DIR = Path(__file__).resolve().parents[2] / ".github" / "ci"
_REPO_ROOT = _CI_DIR.parents[1]
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))

from check_imports import import_checks, main


def _run_lint_imports(config_path: Path) -> subprocess.CompletedProcess[str]:
    lint_imports = Path(sys.executable).with_name("lint-imports")
    return subprocess.run(
        [str(lint_imports), "--config", str(config_path)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_layers_contract_core_platform_colon_allows_cross_imports() -> None:
    """``core : platform`` is documented import-linter syntax for co-dependent siblings.

    ``core | platform`` would forbid legitimate core <-> platform imports and break
    the contract (see import-linter "Multi-item layers" docs).
    """
    strict_text = (_REPO_ROOT / ".importlinter.strict").read_text(encoding="utf-8")
    assert "core : platform" in strict_text

    piped_text = strict_text.replace("core : platform", "core | platform")
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".importlinter.strict",
        dir=_REPO_ROOT,
        delete=False,
    ) as handle:
        handle.write(piped_text)
        piped_config = Path(handle.name)

    try:
        completed = _run_lint_imports(piped_config)
        output = completed.stdout + completed.stderr
        assert completed.returncode != 0
        assert "core is not allowed to import platform" in output
        assert "platform is not allowed to import core" in output
    finally:
        piped_config.unlink(missing_ok=True)


def test_importlinter_strict_config_exists() -> None:
    strict_config = _REPO_ROOT / ".importlinter.strict"
    assert strict_config.is_file(), "make check-layers-strict requires .importlinter.strict"


def test_import_checks_strict_uses_strict_config() -> None:
    strict_config = _REPO_ROOT / ".importlinter.strict"
    with patch("check_imports._run_importlinter", return_value=0) as mock_linter:
        import_checks(strict_layers=True)[1].run()
        mock_linter.assert_called_once_with(config=strict_config)


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


def test_main_strict_passes_with_real_config() -> None:
    assert main(["--strict"]) == 0
