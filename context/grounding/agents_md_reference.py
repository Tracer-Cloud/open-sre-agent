"""AGENTS.md grounding helpers for OpenSRE interactive-shell answers.

The conversational interactive-shell assistant grounds answers on the
``opensre --help`` reference (via :class:`~context.grounding.cli_reference.CliReference`)
and, for procedural questions, excerpts from ``docs/`` (via
:class:`~context.grounding.docs_reference.DocsReference`). Neither surface
includes internal repo-map content, so the assistant cannot answer questions
like "where do I add a new tool?" or "how does the remote threads pipeline
work?" from maintained internal documentation.

This module surfaces the repo's ``AGENTS.md`` files (root + per-package) as a
third grounding source for the conversational shell. It is purely static
(no embeddings, no DB, no new dependencies) and mirrors the shape of
:class:`~context.grounding.docs_reference.DocsReference` so the two stay
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

import os
from dataclasses import dataclass
from pathlib import Path

from context.grounding._cache import FingerprintCache, excerpt
from context.grounding.reference import GroundingReference
from context.models import CacheStats, PromptSection

# Repo root is two levels above this file
# (.../context/grounding/agents_md_reference.py -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]

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


def _format_label(relpath: str) -> str:
    """Header label used in the rendered block.

    The repo-root file is rendered as ``AGENTS.md (root)`` to disambiguate it
    from the per-package files (e.g. ``core/llm/AGENTS.md``).
    """
    if relpath == _AGENTS_MD_FILENAME:
        return f"{_AGENTS_MD_FILENAME} (root)"
    return relpath


class AgentsMdReference(GroundingReference):
    """Session-scoped AGENTS.md discovery + grounding cache.

    Holds its parse cache as instance state so each :class:`GroundingContext`
    owns an isolated cache with no module-level mutable globals.
    """

    name = "agents_md"

    def __init__(self) -> None:
        self._cache: FingerprintCache[AgentsMdFile] = FingerprintCache(
            max_entries=_MAX_AGENTS_MD_FP_CACHE_ENTRIES
        )

    def discover(self, root: Path | None = None) -> list[AgentsMdFile]:
        """Walk the repo root, parse each ``AGENTS.md``, return :class:`AgentsMdFile` records.

        Every call walks the tree (and stats what it finds) — even on cache
        hits — because the walk + per-file fingerprint is what detects in-file
        edits between grounding calls during a long-running shell. The cost is
        bounded by the ``_SKIP_DIRS`` prune (notably ``.venv``).
        """
        target = root if root is not None else _REPO_ROOT
        return self._cache.get_or_parse(
            target, iter_files=_iter_agents_md_files, parse=_parse_agents_md_files
        )

    def build_text(self, *, max_chars: int = _DEFAULT_MAX_TOTAL_CHARS) -> str:
        """Assemble an AGENTS.md reference block for LLM grounding.

        Concatenates one section per discovered file, in sorted relpath order, of
        the form::

            === AGENTS.md (root) ===
            ...
            === core/llm/AGENTS.md ===
            ...

        Returns ``""`` when no AGENTS.md files are available so callers can
        detect that and skip the block entirely.
        """
        files = self.discover()
        if not files:
            return ""

        text = (
            "".join(
                PromptSection(
                    label=_format_label(f.relpath),
                    body=excerpt(f.body, _MAX_PER_FILE_CHARS),
                    style="heading",
                ).render()
                for f in files
            ).rstrip()
            + "\n"
        )
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[... AGENTS.md reference truncated ...]\n"
        return text

    def invalidate(self) -> None:
        """Clear the bounded parse cache (tests, forced refresh)."""
        self._cache.clear()

    def stats(self) -> CacheStats:
        """Debug metrics for AGENTS.md grounding cache (hits/misses/size)."""
        return self._cache.stats(self.name)


__all__ = [
    "AgentsMdFile",
    "AgentsMdReference",
]
