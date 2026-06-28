"""Small value models for interactive-shell grounding diagnostics."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CacheStats(BaseModel):
    """Grounding-cache diagnostics shared by shell reference sources."""

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
        """Compact single-line summary for terminal diagnostics."""
        if self.cached is not None:
            return f"hits={self.hits} misses={self.misses} cached={'yes' if self.cached else 'no'}"
        if self.currsize is not None:
            return f"hits={self.hits} misses={self.misses} entries={self.currsize}/{self.maxsize}"
        return f"hits={self.hits} misses={self.misses}"


__all__ = ["CacheStats"]
