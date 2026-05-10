"""Verbose diagnostics for interactive-shell grounding caches."""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)


def _format_grounding_stats(stats: dict[str, Any]) -> str:
    hits = stats.get("hits", 0)
    misses = stats.get("misses", 0)
    if "cached" in stats:
        return f"hits={hits} misses={misses} cached={'yes' if stats['cached'] else 'no'}"
    if "currsize" in stats and "maxsize" in stats:
        return f"hits={hits} misses={misses} entries={stats['currsize']}/{stats['maxsize']}"
    return str(stats)


@dataclass(frozen=True)
class GroundingSource:
    name: str
    stats_fn: Callable[[], dict[str, Any]]
    format_fn: Callable[[dict[str, Any]], str] = _format_grounding_stats


_GROUNDING_SOURCE_REGISTRY: OrderedDict[str, GroundingSource] = OrderedDict()


def register_grounding_source(source: GroundingSource) -> None:
    _GROUNDING_SOURCE_REGISTRY[source.name] = source


def unregister_grounding_source(name: str) -> None:
    _GROUNDING_SOURCE_REGISTRY.pop(name, None)


def iter_grounding_sources() -> Iterable[GroundingSource]:
    return tuple(_GROUNDING_SOURCE_REGISTRY.values())


def log_grounding_cache_diagnostics(reason: str) -> None:
    """Log CLI/docs grounding cache stats when ``TRACER_VERBOSE=1``."""
    if os.environ.get("TRACER_VERBOSE") != "1":
        return
    rendered = " ".join(f"{source.name}={source.stats_fn()}" for source in iter_grounding_sources())
    _logger.debug(
        "grounding cache [%s] %s",
        reason,
        rendered,
    )


__all__ = [
    "GroundingSource",
    "iter_grounding_sources",
    "log_grounding_cache_diagnostics",
    "register_grounding_source",
    "unregister_grounding_source",
]
