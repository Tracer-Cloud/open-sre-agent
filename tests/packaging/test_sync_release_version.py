from __future__ import annotations

import subprocess
import sys

from config.constants.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "platform" / "packaging" / "sync_release_version.py"


def test_sync_release_version_updates_pyproject() -> None:
    before = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        subprocess.run(
            [sys.executable, str(_SCRIPT), "--version", "0.0.test-sync"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert 'version = "0.0.test-sync"' in (REPO_ROOT / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    finally:
        (REPO_ROOT / "pyproject.toml").write_text(before, encoding="utf-8")
