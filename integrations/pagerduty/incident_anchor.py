"""PagerDuty incident-anchor parser.

Registered with :func:`core.domain.types.incident_anchors.register_anchor_parser`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.domain.types.incident_anchors import SOURCE_FIRED_AT, parse_iso8601


def pagerduty_incident_anchor(payload: dict[str, Any]) -> tuple[datetime, str] | None:
    """PagerDuty incidents carry ``triggered_at`` / ``created_at``.

    Webhook v3 nests the incident under ``event.data``; older v2
    payloads sometimes nest under ``incident``. We probe both shapes
    plus the top level.
    """
    candidates: list[dict[str, Any]] = [payload]
    event = payload.get("event")
    if isinstance(event, dict):
        data = event.get("data")
        if isinstance(data, dict):
            candidates.append(data)
    incident = payload.get("incident")
    if isinstance(incident, dict):
        candidates.append(incident)

    for source in candidates:
        for key in ("triggered_at", "created_at"):
            value = source.get(key)
            if isinstance(value, str):
                anchor = parse_iso8601(value)
                if anchor is not None:
                    return anchor, SOURCE_FIRED_AT
    return None


__all__ = ["pagerduty_incident_anchor"]
