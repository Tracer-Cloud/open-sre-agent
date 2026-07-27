"""Datadog incident-anchor parser.

Registered with :func:`core.domain.types.incident_anchors.register_anchor_parser`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.domain.types.incident_anchors import SOURCE_FIRED_AT, parse_iso8601


def datadog_incident_anchor(payload: dict[str, Any]) -> tuple[datetime, str] | None:
    """Datadog webhook payloads carry ``event_time`` (epoch seconds or
    milliseconds) or ``last_updated``. We treat both as the
    ``alert.firedAt`` anchor since Datadog does not separately expose
    the underlying-condition start time in standard webhook payloads.
    """
    for key in ("event_time", "last_updated", "alert_transition_time"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            # bool is a subtype of int in Python — exclude explicitly.
            continue
        if isinstance(value, (int, float)):
            # Datadog webhooks use milliseconds-since-epoch; tolerate seconds too.
            seconds = float(value) / (1000.0 if value > 1e11 else 1.0)
            try:
                return datetime.fromtimestamp(seconds, tz=UTC), SOURCE_FIRED_AT
            except (OverflowError, OSError, ValueError):
                continue
        if isinstance(value, str):
            anchor = parse_iso8601(value)
            if anchor is not None:
                return anchor, SOURCE_FIRED_AT
    return None


__all__ = ["datadog_incident_anchor"]
