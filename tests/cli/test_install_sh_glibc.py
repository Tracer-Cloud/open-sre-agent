"""Regression tests for the Linux release glibc preflight."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="install.sh is POSIX-only; run the guard tests on Linux/macOS.",
)

INSTALL_SH = Path(__file__).parents[2] / "install.sh"


def _run_glibc_guard(tmp_path: Path, version: str) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    getconf = fake_bin / "getconf"
    getconf.write_text(
        f"#!/usr/bin/env sh\nprintf 'glibc {version}\\n'\n",
        encoding="utf-8",
    )
    getconf.chmod(0o755)

    source = INSTALL_SH.read_text(encoding="utf-8").rsplit('main "$@"', 1)[0]
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        ["bash", "-c", f"{source}\nplatform=linux\ncheck_linux_glibc_compatibility"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_install_sh_glibc_guard_rejects_unsupported_host(tmp_path: Path) -> None:
    result = _run_glibc_guard(tmp_path, "2.31")

    assert result.returncode != 0
    assert "requires glibc >= 2.35" in result.stderr
    assert "detected 2.31" in result.stderr


def test_install_sh_glibc_guard_accepts_supported_host(tmp_path: Path) -> None:
    result = _run_glibc_guard(tmp_path, "2.35")

    assert result.returncode == 0, result.stderr
