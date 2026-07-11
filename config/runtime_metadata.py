"""Safe read-only runtime metadata for sessions and sandboxed agent tools.

Populated at session init so agents can answer introspection questions
(e.g. OpenSRE version) without shelling out. Subprocess remains blocked in
the Python execution sandbox; this is the preferred alternative.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.config import get_environment
from config.version import get_opensre_version

# Reserved key merged into ``execute_python_code`` inputs (never overwrite user keys).
RUNTIME_INPUTS_KEY = "opensre_runtime"

_RELEASE_TAG_PATTERN = re.compile(r"^v\d+\.\d+(\.\d+){2,}$")


def _resolve_gitdir(candidate: Path) -> Path | None:
    """Return the git directory for ``candidate`` (``.git``), or ``None``.

    Handles both a normal checkout (``.git`` is a directory) and a linked
    worktree / submodule (``.git`` is a file with a ``gitdir: <path>`` line).
    """
    if candidate.is_dir():
        return candidate
    if not candidate.is_file():
        return None
    try:
        content = candidate.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("gitdir:"):
            continue
        target = Path(stripped[len("gitdir:") :].strip())
        if not target.is_absolute():
            target = (candidate.parent / target).resolve()
        return target if target.is_dir() else None
    return None


@dataclass(frozen=True)
class _GitLayout:
    """Per-worktree gitdir plus the shared common gitdir.

    In a standard checkout the two are the same directory. In a linked worktree
    (``git worktree add``), ``HEAD`` is per-worktree but ``refs/``, ``packed-refs``,
    and tags live in the primary repo's gitdir named by the worktree's
    ``commondir`` marker file.
    """

    gitdir: Path
    commondir: Path


def _resolve_commondir(gitdir: Path) -> Path:
    """Return the shared common gitdir for ``gitdir``.

    Standard checkouts have no ``commondir`` marker; the gitdir is its own
    common dir. Linked worktrees carry a ``commondir`` file with a path
    (relative to the per-worktree gitdir) to the primary repo's gitdir.
    """
    marker = gitdir / "commondir"
    if not marker.is_file():
        return gitdir
    try:
        content = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return gitdir
    if not content:
        return gitdir
    target = Path(content)
    if not target.is_absolute():
        target = (gitdir / target).resolve()
    return target if target.is_dir() else gitdir


def _find_git_layout() -> _GitLayout | None:
    """Walk up from this file to the enclosing repo's git layout."""
    here = Path(__file__).resolve().parent
    while here.parent != here:
        gitdir = _resolve_gitdir(here / ".git")
        if gitdir is not None:
            return _GitLayout(gitdir=gitdir, commondir=_resolve_commondir(gitdir))
        here = here.parent
    return None


def _read_packed_refs(commondir: Path) -> dict[str, str]:
    """Parse ``<commondir>/packed-refs`` into a ``{ref_name: sha}`` map.

    After ``git pack-refs`` the loose files under ``refs/`` disappear and both
    branch heads and tag refs live only here. Peeled tag lines (``^<sha>``) are
    ignored: the non-peeled line already holds the tag object's sha which is
    enough for a build marker.
    """
    packed = commondir / "packed-refs"
    if not packed.is_file():
        return {}
    refs: dict[str, str] = {}
    try:
        content = packed.read_text(encoding="utf-8")
    except OSError:
        return {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if sha and name:
            refs[name] = sha
    return refs


def _read_ref_sha(layout: _GitLayout, ref_name: str) -> str | None:
    """Resolve ``ref_name`` (e.g. ``refs/heads/main``) via loose files + packed-refs.

    Per-worktree refs (bisect/HEAD-like) may live under the worktree gitdir,
    so it's tried first; branches and tags live in the commondir.
    """
    for base in (layout.gitdir, layout.commondir):
        loose = base / ref_name
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
    return _read_packed_refs(layout.commondir).get(ref_name)


def _read_git_head_sha(layout: _GitLayout) -> str | None:
    """Short SHA the working tree currently points at, or ``None``."""
    head_file = layout.gitdir / "HEAD"
    if not head_file.is_file():
        return None
    head = head_file.read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head[:7] or None
    sha = _read_ref_sha(layout, head[len("ref: ") :].strip())
    return sha[:7] if sha else None


def _release_tag_sort_key(name: str) -> tuple[int, ...] | None:
    """Numeric tuple for a ``v0.1.YYYY.M.D`` tag; ``None`` if not all-numeric.

    Numeric sort so ``v0.1.2026.10.1`` outranks ``v0.1.2026.9.30`` — a
    lexicographic sort would pick the older tag because ``'9' > '1'`` as ASCII.
    """
    parts = name.removeprefix("v").split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def _iter_release_tag_names(commondir: Path) -> set[str]:
    """Release tag names, from loose refs and from ``packed-refs`` combined."""
    names: set[str] = set()
    tags_dir = commondir / "refs" / "tags"
    if tags_dir.is_dir():
        names.update(entry.name for entry in tags_dir.iterdir())
    for ref_name in _read_packed_refs(commondir):
        if ref_name.startswith("refs/tags/"):
            names.add(ref_name[len("refs/tags/") :])
    return names


def _read_latest_release_tag(commondir: Path) -> str | None:
    """Highest release tag (loose + packed) by numeric ordering."""
    ranked: list[tuple[tuple[int, ...], str]] = []
    for name in _iter_release_tag_names(commondir):
        if not _RELEASE_TAG_PATTERN.match(name):
            continue
        key = _release_tag_sort_key(name)
        if key is not None:
            ranked.append((key, name))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][1]


def _detect_build_info() -> str:
    """Human-readable build marker: ``""`` for wheels, ``dev, <tag> @ <sha>`` for checkouts."""
    layout = _find_git_layout()
    if layout is None:
        return ""
    tag = _read_latest_release_tag(layout.commondir)
    sha = _read_git_head_sha(layout)
    if tag and sha:
        return f"dev, {tag} @ {sha}"
    if tag:
        return f"dev, {tag}"
    if sha:
        return f"dev, @ {sha}"
    return "dev"


def build_runtime_metadata() -> dict[str, Any]:
    """JSON-serializable read-only runtime facts for the current process.

    Keys are stable for prompts and sandbox ``inputs``:

    - ``opensre_version`` — package version via ``importlib.metadata``.
    - ``opensre_build`` — ``""`` in released wheels; ``dev, v0.1.YYYY.M.D @ SHA``
      in a git checkout so the LLM can quote the exact build in local dev.
    - ``runtime_env`` — ``OPENSRE_ENV`` env var, else the app environment name.
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

    Never overwrites an existing ``opensre_runtime`` key supplied by the caller.
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
