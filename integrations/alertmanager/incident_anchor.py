"""Alertmanager incident-anchor parser.

Registered with :func:`core.domain.types.incident_anchors.register_anchor_parser`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.domain.types.incident_anchors import SOURCE_STARTS_AT, parse_iso8601


def alertmanager_incident_anchor(payload: dict[str, Any]) -> tuple[datetime, str] | None:
    """Alertmanager / Prometheus payloads carry ``startsAt`` per alert.

    Alertmanager wraps individual alerts in a top-level ``alerts`` list
    (webhook v4) and may also carry a top-level ``startsAt`` (older
    grouped alert payloads). ``startsAt`` is the moment the underlying
    condition began — strictly preferred over ``firedAt``, which is just
    when the rule sent the notification.

    When multiple alerts are present we use the EARLIEST ``startsAt`` so
    the resulting window covers the full firing burst rather than only
    the most recent alert.

    Grafana managed alerts use the Alertmanager webhook schema verbatim, so
    this parser also covers Grafana — no separate Grafana anchor parser is
    needed.
    """
    earliest: datetime | None = None
    if isinstance(payload.get("startsAt"), str):
        anchor = parse_iso8601(payload["startsAt"])
        if anchor is not None:
            earliest = anchor
    alerts = payload.get("alerts")
    if isinstance(alerts, list):
        for alert in alerts:
            if not isinstance(alert, dict):
                continue
            value = alert.get("startsAt")
            if not isinstance(value, str):
                continue
            anchor = parse_iso8601(value)
            if anchor is None:
                continue
            if earliest is None or anchor < earliest:
                earliest = anchor
    if earliest is not None:
        return earliest, SOURCE_STARTS_AT
    return None


__all__ = ["alertmanager_incident_anchor"]
