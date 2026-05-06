"""Cross-platform helpers for ``llm_cli`` tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def write_fake_runnable_cli_bin(tmp_path: Path, stem: str) -> Path:
    """Create a minimal file that passes ``is_runnable_binary`` on this OS.

    On Windows, extensionless scripts are rejected unless ``os.access`` claims
    execute — portable tests use ``.exe`` so resolution matches production rules.
    """
    filename = f"{stem}.exe" if sys.platform == "win32" else stem
    path = tmp_path / filename
    path.write_bytes(b"")
    if sys.platform != "win32":
        os.chmod(path, 0o700)
    return path
