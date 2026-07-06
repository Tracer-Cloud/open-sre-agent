from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "platform" / "packaging" / "sync_release_version.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def test_sync_release_version_updates_pyproject() -> None:
    before = _PYPROJECT.read_text(encoding="utf-8")
    try:
        subprocess.run(
            [sys.executable, str(_SCRIPT), "--version", "0.0.test-sync"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert 'version = "0.0.test-sync"' in _PYPROJECT.read_text(encoding="utf-8")
    finally:
        _PYPROJECT.write_text(before, encoding="utf-8")
