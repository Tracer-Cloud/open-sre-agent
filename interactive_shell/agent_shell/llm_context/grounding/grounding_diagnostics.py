"""Verbose diagnostics for interactive-shell grounding caches."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass
class GroundingSource:
    """A single grounding cache source exposing stats for diagnostics."""

    name: str
    stats_fn: Callable[[], dict[str, Any]]
    format_fn: Callable[[dict[str, Any]], str] = field(
        default_factory=lambda: lambda s: ", ".join(f"{k}={v}" for k, v in s.items())
    )


def log_grounding_cache_diagnostics(sources: Iterable[GroundingSource], reason: str) -> None:
    """Log the provided grounding cache stats when ``TRACER_VERBOSE=1``."""
    if os.environ.get("TRACER_VERBOSE") != "1":
        return
    for source in sources:
        stats = source.stats_fn()
        _logger.debug("grounding cache [%s] %s=%s", reason, source.name, stats)


__all__ = [
    "GroundingSource",
    "log_grounding_cache_diagnostics",
]
