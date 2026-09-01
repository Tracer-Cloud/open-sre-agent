"""OpenSRE package version for CLI, telemetry, and release reporting."""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path

#: Base public version when no full release string is available.
_DEV_BASE_VERSION = "0.1"
_LOCAL_SEGMENT_RE = re.compile(r"[^0-9a-zA-Z]+")


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


def _local_segment(text: str) -> str:
    """PEP 440 local segment: lowercase alphanumerics joined by dots (may be empty)."""
    return _LOCAL_SEGMENT_RE.sub(".", text.lower()).strip(".")


def _dev_build_version(base: str) -> str:
    """Append git build metadata to *base*: ``base.Y.M.D+<branch>.<sha>``.

    Mirrors the release version shape so a dev checkout still reports a specific
    build. Falls back to *base* when git metadata is unreadable (a stripped
    checkout), so a bare install keeps a clean version.
    """
    from config.runtime_metadata.build_info import (
        find_git_layout,
        read_git_head_branch,
        read_git_head_sha,
    )

    layout = find_git_layout()
    if layout is None:
        return base
    sha = read_git_head_sha(layout)
    if not sha:
        return base
    today = datetime.now(tz=UTC)
    date = f"{today.year}.{today.month}.{today.day}"
    branch = _local_segment(read_git_head_branch(layout) or "")
    # Detached HEAD has no branch — use the sha alone rather than invent a name.
    local = f"{branch}.{sha}" if branch else sha
    return f"{base}.{date}+{local}"


def get_opensre_version() -> str:
    """Return the specific build version: the released string, else a git dev build.

    A release build carries the canonical ``0.1.YYYY.M.D+main.<sha>`` string in
    package metadata / ``pyproject`` and is returned verbatim. A dev checkout has
    only the base version, so it is expanded to the same shape from git.
    """
    version = _installed_version() or _pyproject_version()
    if version and "+" in version:
        return version
    return _dev_build_version(version or _DEV_BASE_VERSION)
