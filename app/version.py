"""Version helpers shared by packaged and frozen entrypoints."""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

PACKAGE_NAME = "opensre"
DEFAULT_VERSION = "2026.4.5"
_PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _read_pyproject_version() -> str | None:
    """Return the repo version when running directly from a source checkout."""
    try:
        data = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None

    project = data.get("project")
    if not isinstance(project, dict):
        return None

    version = project.get("version")
    if not isinstance(version, str):
        return None

    version = version.strip()
    return version or None


def get_version() -> str:
    """Return the installed package version, then repo metadata, then a bundled fallback."""
    try:
        return importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return _read_pyproject_version() or DEFAULT_VERSION
