"""Shared routing types for the interactive-shell router.

Extracted from the router module to keep the router entrypoint minimal and free
of import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class RoutingSession(Protocol):
    last_state: dict[str, object] | None


class RouteKind(StrEnum):
    HANDLE_MESSAGE_WITH_AGENT = "handle_message_with_agent"


@dataclass(frozen=True)
class RouteDecision:
    route_kind: RouteKind
    confidence: float
    # Must contain only internal rule names; never user-derived content.
    matched_signals: tuple[str, ...] = ()
    fallback_reason: str | None = None
    # Normalized slash command text to dispatch when route_kind == SLASH.
    command_text: str | None = None

    def to_event_payload(self) -> dict[str, str | bool | int | float]:
        """Structured telemetry/debug payload for route decisions."""
        return {
            "route_kind": self.route_kind.value,
            "confidence": self.confidence,
            "matched_signals": ",".join(self.matched_signals),
            "fallback_reason": self.fallback_reason or "",
        }


__all__ = [
    "RouteDecision",
    "RouteKind",
    "RoutingSession",
]
