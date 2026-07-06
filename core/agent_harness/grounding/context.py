"""Session-scoped grounding context aggregating the LLM grounding caches.

A single :class:`GroundingContext` owns one instance of each cached grounding
reference (docs, AGENTS.md). It is created per ``Session`` and threaded
through prompt assembly, so the grounding caches have a clear, process-scoped
lifetime with no module-level mutable globals.
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
    """Owns the per-session repo-level grounding caches and exposes diagnostics."""

    docs: DocsReference = field(default_factory=DocsReference)
    agents_md: AgentsMdReference = field(default_factory=AgentsMdReference)

    def iter_sources(self) -> list[GroundingSource]:
        """Return each cache as a :class:`GroundingSource` for diagnostics display."""
        return [
            self.docs.as_grounding_source(),
            self.agents_md.as_grounding_source(),
        ]

    def log_cache_diagnostics(self, reason: str) -> None:
        """Log all grounding cache stats when ``TRACER_VERBOSE=1``."""
        log_grounding_cache_diagnostics(self.iter_sources(), reason)

    def invalidate(self) -> None:
        """Drop every grounding cache (tests, forced refresh)."""
        self.docs.invalidate()
        self.agents_md.invalidate()


__all__ = ["GroundingContext"]
