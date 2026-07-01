"""Regression tests for the frozen-binary stdlib ``platform`` loader.

The first-party ``platform`` package shadows the stdlib ``platform`` module and
re-exports its API. In a PyInstaller frozen build the stdlib is not laid out as
loose ``.py`` files, so the release workflow bundles a copy of ``platform.py``
that ``platform/__init__.py`` loads from ``sys._MEIPASS``. These tests guard that
contract so the binary smoke test cannot silently regress again.
"""

from __future__ import annotations

import sys
from pathlib import Path

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()

import platform as _platform_pkg  # noqa: E402  (first-party package after bootstrap)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release.yml"

_FROZEN_STDLIB_DIR = _platform_pkg._FROZEN_STDLIB_DIR
_candidate_stdlib_platform_paths = _platform_pkg._candidate_stdlib_platform_paths
_load_stdlib_platform = _platform_pkg._load_stdlib_platform

_BUNDLED_PLATFORM_SOURCE = "OPENSRE_BUNDLED_MARKER = 'bundled'\n"


def test_first_party_platform_is_a_package_proxying_stdlib() -> None:
    """The shadowing package must still expose the stdlib platform API."""
    assert hasattr(_platform_pkg, "__path__")  # it is a package
    assert _platform_pkg.system()  # stdlib API copied in
    assert _platform_pkg.python_version()


def test_candidate_paths_prioritize_meipass(monkeypatch, tmp_path) -> None:
    """Frozen builds must probe the bundled copy under ``sys._MEIPASS`` first."""
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    candidates = _candidate_stdlib_platform_paths()

    assert candidates[0] == tmp_path / _FROZEN_STDLIB_DIR / "platform.py"


def test_candidate_paths_without_meipass_resolve_real_stdlib(monkeypatch) -> None:
    """Source checkouts must still resolve the genuine stdlib ``platform.py``."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    candidates = _candidate_stdlib_platform_paths()

    assert candidates  # at least the sysconfig location
    assert any(path.is_file() for path in candidates)


def test_load_stdlib_platform_uses_bundled_copy_when_frozen(monkeypatch, tmp_path) -> None:
    """When frozen, the loader must read the bundled copy, not a system path."""
    bundled_dir = tmp_path / _FROZEN_STDLIB_DIR
    bundled_dir.mkdir()
    (bundled_dir / "platform.py").write_text(_BUNDLED_PLATFORM_SOURCE, encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    module = _load_stdlib_platform()

    assert getattr(module, "OPENSRE_BUNDLED_MARKER", None) == "bundled"


def test_release_workflow_bundles_stdlib_platform() -> None:
    """The release build must stage and bundle ``platform.py`` for the binary.

    This ties the workflow's ``--add-data`` destination to ``_FROZEN_STDLIB_DIR``
    so renaming the constant without updating the workflow fails fast instead of
    only surfacing as a release-time binary crash.
    """
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "platform.py" in workflow  # staging step copies the stdlib module
    assert ".stdlib_vendor" in workflow  # staged into a relative dir
    assert _FROZEN_STDLIB_DIR in workflow  # bundled under the loader's dest dir
