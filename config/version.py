"""OpenSRE package version for CLI, telemetry, and release reporting."""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path


def get_opensre_version() -> str:
    """Return the installed package version, else checkout metadata, else the dev fallback."""
    try:
        return importlib.metadata.version("opensre")
    except importlib.metadata.PackageNotFoundError:
        pass

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project")
        if isinstance(project, dict):
            version = project.get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        pass

    return "0.1"
