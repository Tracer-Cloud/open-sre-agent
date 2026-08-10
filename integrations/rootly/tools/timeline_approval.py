"""How ``rootly_post_timeline_event`` describes itself in an approval prompt.

The reviewer's question is not "which fields were passed" — it is *what will
appear on the incident, and who will see it*. Visibility is the whole decision:
``external`` publishes to a customer-facing timeline and cannot be quietly
walked back. So the prompt leads with it and shows the note verbatim.

This lives in ``integrations/rootly`` rather than in the gateway because
knowing what ``visibility`` means is Rootly knowledge, and the approval surface
must stay vendor-neutral.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from integrations.rootly.client import normalize_visibility

# Read at a glance in a chat message, so every field is bounded. The approval
# surface clamps the whole rendering again as a backstop.
_ID_LIMIT = 60
_EVENT_LIMIT = 600

_EXTERNAL_WARNING = "external — this publishes to the customer-facing status timeline"
_INTERNAL_NOTE = "internal — visible to responders only"


def _one_line(value: Any, *, limit: int) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _incident(arguments: Mapping[str, Any]) -> str:
    return _one_line(arguments.get("incident_id"), limit=_ID_LIMIT) or "(incident not set)"


def _event_text(arguments: Mapping[str, Any]) -> str:
    text = str(arguments.get("event") or "").strip()
    if not text:
        return "(no text supplied)"
    return text if len(text) <= _EVENT_LIMIT else f"{text[:_EVENT_LIMIT]}…"


class TimelineEventApprovalDisplay:
    """Renders the timeline-event approval prompt and its outcome."""

    def headline(self, arguments: Mapping[str, Any]) -> str:
        """``Post Rootly timeline event — incident <id>``."""
        return f"Post Rootly timeline event — incident {_incident(arguments)}"

    def details(self, arguments: Mapping[str, Any]) -> str:
        """Visibility first, then the note exactly as it will be written."""
        visibility = normalize_visibility(str(arguments.get("visibility") or ""))
        banner = _EXTERNAL_WARNING if visibility == "external" else _INTERNAL_NOTE
        return "\n".join([banner, "", _event_text(arguments)])

    def receipt(self, arguments: Mapping[str, Any], result: Mapping[str, Any]) -> str:
        """Name the event Rootly created, once it has answered."""
        event_id = str(result.get("event_id") or "").strip()
        if not event_id:
            return ""
        visibility = str(result.get("visibility") or "").strip() or "internal"
        return (
            f"Posted Rootly timeline event {event_id} ({visibility}) "
            f"on incident {_incident(arguments)}"
        )


TIMELINE_EVENT_APPROVAL_DISPLAY = TimelineEventApprovalDisplay()

__all__ = ["TIMELINE_EVENT_APPROVAL_DISPLAY", "TimelineEventApprovalDisplay"]
