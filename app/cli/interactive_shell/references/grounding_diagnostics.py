"""Verbose diagnostics for interactive-shell grounding caches."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)

_registry: dict[str, GroundingSource] = {}


@dataclass
class GroundingSource:
    """A single grounding cache source that can self-register."""

    name: str
    stats_fn: Callable[[], dict[str, Any]]
    format_fn: Callable[[dict[str, Any]], str] = field(
        default_factory=lambda: lambda s: ", ".join(f"{k}={v}" for k, v in s.items())
    )


def register_grounding_source(source: GroundingSource) -> None:
    """Register a grounding source. Idempotent — same name updates in place."""
    _registry[source.name] = source


def iter_grounding_sources() -> list[GroundingSource]:
    """Return a snapshot of registered grounding sources in insertion order."""
    return list(_registry.values())


def log_grounding_cache_diagnostics(reason: str) -> None:
    """Log all registered grounding cache stats when ``TRACER_VERBOSE=1``."""
    if os.environ.get("TRACER_VERBOSE") != "1":
        return
    for source in iter_grounding_sources():
        stats = source.stats_fn()
        _logger.debug("grounding cache [%s] %s=%s", reason, source.name, stats)


__all__ = [
    "GroundingSource",
    "register_grounding_source",
    "iter_grounding_sources",
    "log_grounding_cache_diagnostics",
]
