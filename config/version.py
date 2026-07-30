"""OpenSRE package version for CLI, telemetry, and release reporting."""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path


def _installed_version() -> str | None:
    try:
        return importlib.metadata.version("opensre")
    except importlib.metadata.PackageNotFoundError:
        return None


def _pyproject_version() -> str | None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project")
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None
    if isinstance(project, dict):
        version = project.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    return None


_version_cache: str | None = None


def get_opensre_version() -> str:
    """Return the installed package version, else checkout metadata, else the dev fallback.

    Cached for the process lifetime — the package version does not change mid-run.
    """
    global _version_cache
    if _version_cache is not None:
        return _version_cache
    value = _installed_version() or _pyproject_version() or "0.1"
    _version_cache = value
    return value
