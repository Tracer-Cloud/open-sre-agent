"""Regression tests for the Linux release glibc preflight."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="install.sh is POSIX-only; run the guard tests on Linux/macOS.",
)

INSTALL_SH = Path(__file__).parents[2] / "install.sh"


def _run_glibc_guard(
    tmp_path: Path, version: str | None, *, ldd_output: str | None = None
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    getconf = fake_bin / "getconf"
    if version is None:
        getconf.write_text("#!/usr/bin/env sh\nexit 1\n", encoding="utf-8")
    else:
        getconf.write_text(
            f"#!/usr/bin/env sh\nprintf 'glibc {version}\\n'\n",
            encoding="utf-8",
        )
    getconf.chmod(0o755)

    ldd = fake_bin / "ldd"
    if ldd_output is None:
        ldd.write_text("#!/usr/bin/env sh\nexit 1\n", encoding="utf-8")
    else:
        ldd.write_text(
            f"#!/usr/bin/env sh\nprintf '%s' {shlex.quote(ldd_output)}\n",
            encoding="utf-8",
        )
    ldd.chmod(0o755)

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


def test_install_sh_glibc_guard_rejects_unknown_libc(tmp_path: Path) -> None:
    result = _run_glibc_guard(tmp_path, None)

    assert result.returncode != 0
    assert "Could not determine the host glibc version" in result.stderr
    assert "install from source with uv" in result.stderr


def test_install_sh_glibc_guard_accepts_gnu_ldd_fallback(tmp_path: Path) -> None:
    result = _run_glibc_guard(
        tmp_path,
        None,
        ldd_output="ldd (Ubuntu GLIBC 2.35-0ubuntu3) 2.35\n",
    )

    assert result.returncode == 0, result.stderr


def test_install_sh_glibc_guard_rejects_non_glibc_ldd_banner(tmp_path: Path) -> None:
    result = _run_glibc_guard(
        tmp_path,
        None,
        ldd_output="musl libc (x86_64) 1.2.4\n",
    )

    assert result.returncode != 0
    assert "Could not determine the host glibc version" in result.stderr
