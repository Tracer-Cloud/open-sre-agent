"""Cache-stats value types for the interactive-shell grounding reference sources.

Each grounding reference source (AGENTS.md, docs, CLI help) reports its cache
health as a :class:`CacheStats` so the shell can log uniform hit/miss
diagnostics regardless of which caching strategy that source uses.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CacheStats(BaseModel):
    """One reference source's cache health: hit/miss counts, plus size or freshness.

    Always set ``name`` plus ``hits``/``misses`` (both default to 0 for a
    fresh cache). Populate exactly one of these two optional groups to match
    the caching strategy in use, leave the other group at its ``None`` default:

    * ``currsize``/``maxsize``: a bounded (LRU-style) cache, reporting how
      many entries are stored against the cap.
    * ``cached``/``signature``/``created_at_monotonic``: a single cached
      value, reporting whether it is currently populated and when it was
      last (re)built.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    hits: int = 0
    misses: int = 0
    currsize: int | None = None
    maxsize: int | None = None
    cached: bool | None = None
    signature: str | None = None
    created_at_monotonic: float | None = None

    def render(self) -> str:
        """Return one line like ``hits=N misses=N ...`` for log output.

        Appends ``entries=currsize/maxsize`` when the bounded-cache fields
        are set, ``cached=yes|no`` when the single-value-cache fields are
        set, or neither suffix when only hits/misses are populated.
        """
        if self.cached is not None:
            return f"hits={self.hits} misses={self.misses} cached={'yes' if self.cached else 'no'}"
        if self.currsize is not None:
            return f"hits={self.hits} misses={self.misses} entries={self.currsize}/{self.maxsize}"
        return f"hits={self.hits} misses={self.misses}"


__all__ = ["CacheStats"]
