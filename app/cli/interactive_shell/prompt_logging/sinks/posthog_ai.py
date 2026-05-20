"""PostHog sink for `$ai_generation` events."""

from __future__ import annotations

from app.analytics.events import Event
from app.analytics.provider import get_analytics


def capture_ai_generation(properties: dict[str, object]) -> None:
    get_analytics().capture(Event.AI_GENERATION, properties)
