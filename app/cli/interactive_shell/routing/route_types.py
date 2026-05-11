"""Shared routing types for the interactive-shell router and intent classifier.

Extracted from the router module to break the cyclic import between the router
and ``llm_intent_classifier``.  Both modules import from here instead of from
each other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class RoutingSession(Protocol):
    last_state: dict[str, object] | None


class RouteKind(StrEnum):
    SLASH = "slash"
    CLI_HELP = "cli_help"
    CLI_AGENT = "cli_agent"
    NEW_ALERT = "new_alert"
    FOLLOW_UP = "follow_up"


@dataclass(frozen=True)
class RouteDecision:
    route_kind: RouteKind
    confidence: float
    # Must contain only internal rule names; never user-derived content.
    matched_signals: tuple[str, ...] = ()
    fallback_reason: str | None = None

    def to_event_payload(self) -> dict[str, str | bool | int | float]:
        """Structured telemetry/debug payload for route decisions."""
        return {
            "route_kind": self.route_kind.value,
            "confidence": self.confidence,
            "matched_signals": ",".join(self.matched_signals),
            "fallback_reason": self.fallback_reason or "",
        }


@dataclass(frozen=True)
class RouteRule:
    name: str
    route_kind: RouteKind
    confidence: float
    matcher: Callable[[str, RoutingSession], bool]


__all__ = [
    "RouteDecision",
    "RouteKind",
    "RouteRule",
    "RoutingSession",
]
