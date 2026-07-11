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


def _resolve_gitdir(candidate: Path) -> Path | None:
    """Resolve a candidate ``.git`` entry to the actual git directory.

    In a normal checkout ``.git`` is a directory. In a linked worktree or a
    submodule ``.git`` is a *file* containing a ``gitdir: <absolute path>``
    line pointing to the real git dir under the primary repo. Both shapes
    need to work so build metadata surfaces correctly for worktree devs too.
    """
    if candidate.is_dir():
        return candidate
    if candidate.is_file():
        try:
            content = candidate.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("gitdir:"):
                target = Path(line[len("gitdir:") :].strip())
                if not target.is_absolute():
                    target = (candidate.parent / target).resolve()
                if target.is_dir():
                    return target
                return None
    return None


def _repo_root_from_here() -> tuple[Path, Path] | None:
    """Walk up from this file looking for a ``.git`` directory or file.

    Filesystem-only — no subprocess. Used to enrich metadata when opensre is
    imported from a git checkout (local dev). Returns ``None`` in installed
    wheels where ``.git`` doesn't exist alongside the package.

    Returns a tuple of ``(worktree_root, gitdir)`` — the worktree root is
    where ``.git`` lives (a checkout or a linked worktree); the gitdir is the
    resolved directory that actually contains ``HEAD``, ``refs/``, etc. Under
    a normal checkout the two point at the same physical directory
    (``<repo>/.git``); under a linked worktree they differ.
    """
    here = Path(__file__).resolve().parent
    while here.parent != here:
        gitdir = _resolve_gitdir(here / ".git")
        if gitdir is not None:
            return here, gitdir
        here = here.parent
    return None


def _read_git_head_sha(gitdir: Path) -> str | None:
    """Return the short SHA the working tree currently points at, or None."""
    head_file = gitdir / "HEAD"
    if not head_file.is_file():
        return None
    head = head_file.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref_path = gitdir / head[5:].strip()
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8").strip()[:7] or None
        return None
    return head[:7] or None


def _release_tag_sort_key(name: str) -> tuple[int, ...]:
    """Return a tuple sort key for a release tag like ``v0.1.2026.9.30``.

    Sorts by (major, minor, year, month, day...) numerically so
    ``v0.1.2026.10.1`` correctly outranks ``v0.1.2026.9.30`` — a naive
    lexicographic sort would pick the older tag because ``'9' > '1'`` as
    ASCII, flipping September-30 above October-1.
    """
    parts = name.removeprefix("v").split(".")
    numeric: list[int] = []
    for part in parts:
        try:
            numeric.append(int(part))
        except ValueError:
            # Any non-numeric component (unexpected for this tag shape) sorts
            # this tag below any tag whose components are all-numeric.
            return ()
    return tuple(numeric)


def _read_latest_release_tag(gitdir: Path) -> str | None:
    """Return the highest release tag (``v0.1.YYYY.M.D`` shape) present.

    Filesystem-only: reads ``<gitdir>/refs/tags/``. Sorts using a numeric
    tuple key so month/day boundary crossings (e.g. ``9.30`` vs ``10.1``)
    order correctly, unlike lexicographic sort.
    """
    tags_dir = gitdir / "refs" / "tags"
    if not tags_dir.is_dir():
        return None
    candidates = [t.name for t in tags_dir.iterdir() if _RELEASE_TAG_PATTERN.match(t.name)]
    if not candidates:
        return None
    candidates.sort(key=_release_tag_sort_key, reverse=True)
    return candidates[0]


def _detect_build_info() -> str:
    """Return a human-readable build marker for the running process.

    - Installed wheel (no ``.git`` present): returns ``""`` — the caller
      relies on ``opensre_version`` alone (already the full release version
      because ``sync_release_version.py`` stamped ``pyproject.toml`` at build).
    - Git checkout (local dev): returns e.g. ``"dev, v0.1.2026.7.11 @ abc1234"``
      so the LLM can quote the latest tag AND the current SHA without shelling
      out. Both are filesystem reads only.
    """
    located = _repo_root_from_here()
    if located is None:
        return ""
    _, gitdir = located
    latest_tag = _read_latest_release_tag(gitdir)
    sha = _read_git_head_sha(gitdir)
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
