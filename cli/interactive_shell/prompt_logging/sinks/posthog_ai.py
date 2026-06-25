"""PostHog sink for `$ai_generation` events."""

from __future__ import annotations

from platform_services.analytics.events import Event
from platform_services.analytics.provider import JsonValue, get_analytics


def capture_ai_generation(properties: dict[str, JsonValue]) -> None:
    get_analytics().capture(Event.AI_GENERATION, properties)
