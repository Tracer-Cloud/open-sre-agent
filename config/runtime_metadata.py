"""Safe read-only runtime metadata for sessions and sandboxed agent tools.

Populated at session init so agents can answer introspection questions
(e.g. OpenSRE version) without shelling out. Subprocess remains blocked in
the Python execution sandbox; this is the preferred alternative.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from config.config import get_environment
from config.version import get_opensre_version

# Reserved key merged into ``execute_python_code`` inputs (never overwrite user keys).
RUNTIME_INPUTS_KEY = "opensre_runtime"

_RELEASE_TAG_PATTERN = re.compile(r"^v\d+\.\d+(\.\d+){2,}$")


def _repo_root_from_here() -> Path | None:
    """Walk up from this file looking for a ``.git`` directory.

    Filesystem-only — no subprocess. Used to enrich metadata when opensre is
    imported from a git checkout (local dev). Returns ``None`` in installed
    wheels where ``.git`` doesn't exist alongside the package.
    """
    here = Path(__file__).resolve().parent
    while here.parent != here:
        if (here / ".git").is_dir():
            return here
        here = here.parent
    return None


def _read_git_head_sha(repo: Path) -> str | None:
    """Return the short SHA the working tree currently points at, or None."""
    head_file = repo / ".git" / "HEAD"
    if not head_file.is_file():
        return None
    head = head_file.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref_path = repo / ".git" / head[5:].strip()
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8").strip()[:7] or None
        return None
    return head[:7] or None


def _read_latest_release_tag(repo: Path) -> str | None:
    """Return the highest-sorted release tag (v0.1.YYYY.M.D shape) present.

    Filesystem-only: reads ``.git/refs/tags/``. Sorts lexicographically —
    correct for the date-suffixed tag shape opensre uses.
    """
    tags_dir = repo / ".git" / "refs" / "tags"
    if not tags_dir.is_dir():
        return None
    matches = sorted(
        (t.name for t in tags_dir.iterdir() if _RELEASE_TAG_PATTERN.match(t.name)),
        reverse=True,
    )
    return matches[0] if matches else None


def _detect_build_info() -> str:
    """Return a human-readable build marker for the running process.

    - Installed wheel (no ``.git`` present): returns ``""`` — the caller
      relies on ``opensre_version`` alone (already the full release version
      because ``sync_release_version.py`` stamped ``pyproject.toml`` at build).
    - Git checkout (local dev): returns e.g. ``"dev, v0.1.2026.7.11 @ abc1234"``
      so the LLM can quote the latest tag AND the current SHA without shelling
      out. Both are filesystem reads only.
    """
    repo = _repo_root_from_here()
    if repo is None:
        return ""
    latest_tag = _read_latest_release_tag(repo)
    sha = _read_git_head_sha(repo)
    if latest_tag and sha:
        return f"dev, {latest_tag} @ {sha}"
    if latest_tag:
        return f"dev, {latest_tag}"
    if sha:
        return f"dev, @ {sha}"
    return "dev"


def build_runtime_metadata() -> dict[str, Any]:
    """Return JSON-serializable read-only runtime facts for the current process.

    Keys are stable for prompts and sandbox ``inputs``:
    - ``opensre_version`` — installed/package version via ``importlib.metadata``
    - ``opensre_build`` — build marker: empty in released wheels (version is
      already the full release), or ``"dev, v0.1.YYYY.M.D @ SHA"`` in a git
      checkout so the LLM can answer "which build" in local dev.
    - ``runtime_env`` — ``OPENSRE_ENV`` or app environment (development/production)
    """
    env_override = (os.environ.get("OPENSRE_ENV") or "").strip()
    return {
        "opensre_version": get_opensre_version(),
        "opensre_build": _detect_build_info(),
        "runtime_env": env_override or get_environment().value,
    }


def merge_runtime_into_inputs(
    inputs: dict[str, Any] | None,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy ``inputs`` and inject runtime metadata under :data:`RUNTIME_INPUTS_KEY`.

    Does not overwrite an existing ``opensre_runtime`` key supplied by the caller.
    """
    merged: dict[str, Any] = dict(inputs or {})
    if RUNTIME_INPUTS_KEY not in merged:
        merged[RUNTIME_INPUTS_KEY] = dict(metadata or build_runtime_metadata())
    return merged


__all__ = [
    "RUNTIME_INPUTS_KEY",
    "build_runtime_metadata",
    "merge_runtime_into_inputs",
]
