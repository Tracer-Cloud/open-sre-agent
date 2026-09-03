"""Session-scoped grounding context for repo-level reference caches.

Holds one :class:`DocsReference` and one :class:`AgentsMdReference` per
session. Created once per ``Session`` and threaded through prompt assembly
so callers always get a single, consistent view of the grounding data.

**What lives here:** per-session caches for docs and AGENTS.md grounding.
**What must NOT live here:** LLM completions, tool outputs, or any data
that belongs to a specific turn rather than the session setup.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.agent_harness.grounding.agents_md_reference import (
    AgentsMdReference,
)
from core.agent_harness.grounding.diagnostics import (
    GroundingSource,
    log_grounding_cache_diagnostics,
)
from core.agent_harness.grounding.docs_reference import DocsReference


@dataclass
class GroundingContext:
    """Per-session container for repo-level grounding caches.

    Owns exactly one :class:`DocsReference` and one :class:`AgentsMdReference`.
    Callers must not store turn-scoped data (tool outputs, LLM
    completions, run state) here — those do not belong on the session
    grounding cache.

    Attributes:
        docs: Cached reference to the project documentation.
        agents_md: Cached reference to the AGENTS.md repo map.
    """

    docs: DocsReference = field(default_factory=DocsReference)
    agents_md: AgentsMdReference = field(default_factory=AgentsMdReference)

    def iter_sources(self) -> list[GroundingSource]:
        """Return both caches as :class:`GroundingSource` objects for diagnostics.

        Returns:
            A list containing the docs and agents_md sources, in that order.
            Always returns exactly two items.
        """
        return [
            self.docs.as_grounding_source(),
            self.agents_md.as_grounding_source(),
        ]

    def log_cache_diagnostics(self, reason: str) -> None:
        """Emit cache hit/miss stats for all grounding sources when ``TRACER_VERBOSE=1``.

        Args:
            reason: Human-readable label describing why diagnostics are being
                logged (e.g. ``"pre-prompt"`` or ``"post-invalidation"``).
        """
        log_grounding_cache_diagnostics(self.iter_sources(), reason)

    def invalidate(self) -> None:
        """Evict all cached grounding data, forcing a fresh load on next access.

        Use in tests or when a forced cache refresh is required. Invalidates
        both ``docs`` and ``agents_md`` caches. Does not raise if caches are
        already empty.
        """
        self.docs.invalidate()
        self.agents_md.invalidate()


__all__ = ["GroundingContext"]
