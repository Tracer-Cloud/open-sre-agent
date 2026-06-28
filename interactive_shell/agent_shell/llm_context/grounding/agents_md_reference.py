"""AGENTS.md grounding helpers for OpenSRE interactive-shell answers.

The conversational interactive-shell assistant grounds answers on the
``opensre --help`` reference (via :class:`~interactive_shell.agent_shell.llm_context.grounding.cli_reference.CliReference`)
and, for procedural questions, excerpts from ``docs/`` (via
:class:`~interactive_shell.agent_shell.llm_context.grounding.docs_reference.DocsReference`). Neither surface
includes internal repo-map content, so the assistant cannot answer questions
like "where do I add a new tool?" or "how does the remote threads pipeline
work?" from maintained internal documentation.

This module surfaces the repo's ``AGENTS.md`` files (root + per-package) as a
third grounding source for the conversational shell. It is purely static
(no embeddings, no DB, no new dependencies) and mirrors the shape of
:class:`~interactive_shell.agent_shell.llm_context.grounding.docs_reference.DocsReference` so the two stay
symmetric.

Source of truth
---------------
Every ``AGENTS.md`` file under the repository root. We skip ``tests/``,
``node_modules``, ``.git``, ``__pycache__``, and ``.venv`` so we never pull
test-fixture or installed-package content into the prompt.

How files stay fresh
--------------------
Files are parsed lazily and cached on each :class:`AgentsMdReference` instance
keyed by the resolved repo root and a lightweight fingerprint of each tracked
file (relative path, size, ``st_mtime_ns``). Edits to ``AGENTS.md`` files
during a long-running shell invalidate the fingerprint and trigger a re-parse
on the next grounding call. There is no on-disk cache. Use
:meth:`AgentsMdReference.invalidate` in tests to clear the parse cache between
cases.

When files are missing
----------------------
For non-editable installs that do not ship ``AGENTS.md`` files the discovery
returns an empty list and :meth:`AgentsMdReference.build_text` returns an
empty string so callers can detect that and skip the block.
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from interactive_shell.agent_shell.llm_context.grounding.grounding_diagnostics import (
    GroundingSource,
)

# Repo root is five levels above this file
# (.../interactive_shell/agent_shell/llm_context/grounding/agents_md_reference.py -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[4]

_AGENTS_MD_FILENAME = "AGENTS.md"

# Directories whose subtrees never contain AGENTS.md content meant for
# grounding. ``tests`` is excluded by spec (test-fixture AGENTS.md files
# would pollute the assistant's repo map). ``.venv`` is excluded so we don't
# surface installed-package AGENTS.md from third-party dependencies — without
# this, ``os.walk`` would also spend most of its time descending the venv.
_SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        "tests",
        ".venv",
    }
)

# Per-file excerpt cap; total cap is enforced by AgentsMdReference.build_text.
# AGENTS.md files are typically small repo-map docs, so 2K per file gives
# headroom for the root file (which tends to be the largest) without
# crowding the prompt.
_MAX_PER_FILE_CHARS = 2_000
_DEFAULT_MAX_TOTAL_CHARS = 6_000


@dataclass(frozen=True)
class AgentsMdFile:
    """A single ``AGENTS.md`` file available for grounding."""

    relpath: str
    """Path relative to the repo root, with forward slashes (``"AGENTS.md"`` for the root file)."""

    body: str
    """File body, verbatim. AGENTS.md is plain Markdown — no frontmatter to strip."""


def _iter_agents_md_files(root: Path) -> list[Path]:
    """Walk ``root`` collecting ``AGENTS.md`` files, pruning skip dirs in-place.

    ``os.walk`` with ``dirnames[:] = ...`` pruning is meaningfully faster than
    ``rglob`` here because the repo root contains a multi-GB ``.venv`` whose
    subtree we never need to descend.
    """
    if not root.exists() or not root.is_dir():
        return []
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if _AGENTS_MD_FILENAME in filenames:
            files.append(Path(dirpath) / _AGENTS_MD_FILENAME)
    return sorted(files)


# Delimiters keep SHA-256 input unambiguous across (relpath, size, mtime)
# tuple boundaries, mirroring docs_reference for symmetry.
_FP_FIELD_SEP = b"\x00"
_FP_RECORD_SEP = b"\xff"


def _fingerprint_from_paths(root: Path, files: list[Path]) -> str:
    """Digest of tracked AGENTS.md files using paths from a single tree walk."""
    digest = hashlib.sha256()
    if not root.exists() or not root.is_dir():
        digest.update(b"nodir")
        digest.update(_FP_FIELD_SEP)
        digest.update(str(root.resolve() if root.exists() else root).encode())
        digest.update(_FP_FIELD_SEP)
        return digest.hexdigest()

    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            st = path.stat()
            digest.update(rel.encode())
            digest.update(_FP_FIELD_SEP)
            digest.update(str(st.st_size).encode())
            digest.update(_FP_FIELD_SEP)
            digest.update(str(st.st_mtime_ns).encode())
            digest.update(_FP_RECORD_SEP)
        except OSError:
            continue
    return digest.hexdigest()


def _parse_agents_md_files(root: Path, files: list[Path]) -> tuple[AgentsMdFile, ...]:
    if not root.exists() or not root.is_dir():
        return ()
    parsed: list[AgentsMdFile] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        relpath = path.relative_to(root).as_posix()
        parsed.append(AgentsMdFile(relpath=relpath, body=text))
    return tuple(parsed)


# Distinct (root_key, fingerprint) entries retained per instance under churn.
# Eviction drops oldest keys; a reverted tree re-parses once then stays hot.
_MAX_AGENTS_MD_FP_CACHE_ENTRIES = 32


def _excerpt(body: str, max_chars: int = _MAX_PER_FILE_CHARS) -> str:
    """Trim an AGENTS.md body to ``max_chars``, preferring to cut at a paragraph boundary."""
    body = body.strip()
    if len(body) <= max_chars:
        return body
    cutoff = body.rfind("\n\n", 0, max_chars)
    if cutoff < max_chars // 2:
        cutoff = max_chars
    return body[:cutoff].rstrip() + "\n\n[... excerpt truncated ...]\n"


def _format_label(relpath: str) -> str:
    """Header label used in the rendered block.

    The repo-root file is rendered as ``AGENTS.md (root)`` to disambiguate it
    from the per-package files (e.g. ``core/runtime/llm/AGENTS.md``).
    """
    if relpath == _AGENTS_MD_FILENAME:
        return f"{_AGENTS_MD_FILENAME} (root)"
    return relpath


class AgentsMdReference:
    """Session-scoped AGENTS.md discovery + grounding cache.

    Holds its parse cache as instance state so each :class:`GroundingContext`
    owns an isolated cache with no module-level mutable globals.
    """

    name = "agents_md"

    def __init__(self) -> None:
        self._parse_cache: OrderedDict[tuple[str, str], tuple[AgentsMdFile, ...]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def discover(self, root: Path | None = None) -> list[AgentsMdFile]:
        """Walk the repo root, parse each ``AGENTS.md``, return :class:`AgentsMdFile` records."""
        target = root if root is not None else _REPO_ROOT
        resolved = target.resolve() if target.exists() else target
        root_key = str(resolved)

        # Every discover call walks the tree (and stats what it finds) — even on
        # cache hits — because the walk + per-file fingerprint is what detects
        # in-file edits between grounding calls during a long-running shell.
        # Skipping the walk on cache hits would make AGENTS.md edits invisible
        # until eviction, which is the bug the fingerprint design in
        # docs_reference.py was introduced to avoid; we keep the same trade-off
        # here so the two grounding sources stay symmetric. The cost is bounded
        # by the _SKIP_DIRS prune (notably ``.venv``).
        files = _iter_agents_md_files(resolved)
        fp = _fingerprint_from_paths(resolved, files)
        cache_key = (root_key, fp)

        cached = self._parse_cache.get(cache_key)
        if cached is not None:
            self._hits += 1
            self._parse_cache.move_to_end(cache_key)
            return list(cached)

        self._misses += 1
        parsed_tuple = _parse_agents_md_files(resolved, files)

        while len(self._parse_cache) >= _MAX_AGENTS_MD_FP_CACHE_ENTRIES:
            self._parse_cache.popitem(last=False)
        self._parse_cache[cache_key] = parsed_tuple
        return list(parsed_tuple)

    def build_text(self, *, max_chars: int = _DEFAULT_MAX_TOTAL_CHARS) -> str:
        """Assemble an AGENTS.md reference block for LLM grounding.

        Concatenates one section per discovered file, in sorted relpath order, of
        the form::

            === AGENTS.md (root) ===
            ...
            === core/runtime/llm/AGENTS.md ===
            ...

        Returns ``""`` when no AGENTS.md files are available so callers can
        detect that and skip the block entirely.
        """
        files = self.discover()
        if not files:
            return ""

        parts: list[str] = []
        for f in files:
            parts.append(f"=== {_format_label(f.relpath)} ===\n")
            parts.append(_excerpt(f.body))
            parts.append("\n\n")

        text = "".join(parts).rstrip() + "\n"
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[... AGENTS.md reference truncated ...]\n"
        return text

    def invalidate(self) -> None:
        """Clear the bounded parse cache (tests, forced refresh)."""
        self._parse_cache.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict[str, Any]:
        """Debug metrics for AGENTS.md grounding cache (hits/misses/size)."""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "currsize": len(self._parse_cache),
            "maxsize": _MAX_AGENTS_MD_FP_CACHE_ENTRIES,
        }

    def as_grounding_source(self) -> GroundingSource:
        return GroundingSource(
            name=self.name,
            stats_fn=self.stats,
            format_fn=lambda s: (
                f"hits={s['hits']} misses={s['misses']} entries={s['currsize']}/{s['maxsize']}"
            ),
        )


__all__ = [
    "AgentsMdFile",
    "AgentsMdReference",
]
