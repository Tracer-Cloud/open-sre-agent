"""Alert-format anchor parsers for incident window resolution.

Each parser inspects a normalised alert payload dict and returns
``(anchor_datetime, source_label)`` when it finds a timestamp it trusts.
``resolve_incident_window`` in ``incident_window.py`` calls
:func:`extract_anchor` and applies lookback/buffer semantics on top.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Recognised source labels returned by anchor parsers -----------------------
SOURCE_STARTS_AT = "alert.startsAt"
SOURCE_FIRED_AT = "alert.firedAt"
SOURCE_ACTIVATED_AT = "alert.activatedAt"

_CLOUDWATCH_MAX_DEPTH = 4
"""Maximum nesting depth probed by ``_cloudwatch_anchor``.

Real-world CloudWatch payloads are at most 2 levels deep (SNS ``Message``
or EventBridge ``alarmData`` -> alarm dict). The cap is set generously to
4 so legitimate payloads always parse, while pathologically nested input
(``{"Message": {"Message": {"Message": ...}}}``) cannot recurse into
stack overflow.
"""


def parse_iso8601(value: str) -> datetime | None:
    """Parse ISO-8601 timestamps. Naive input is treated as UTC.

    Returns ``None`` for empty or malformed input rather than raising.
    The pipeline must never fail because an upstream alert payload was
    slightly off-spec.

    Accepts the trailing ``Z`` shorthand that ``datetime.fromisoformat``
    rejects on Python < 3.11 and that some vendor payloads use anyway.
    """
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def coerce_alert_dict(raw_alert: Any) -> dict[str, Any]:
    """Normalise ``raw_alert`` into a dict.

    ``state["raw_alert"]`` is typed as ``str | dict``. JSON-string
    payloads (common from webhooks) are parsed; non-dict / un-parseable
    values become an empty dict. Anchor parsers always operate on a dict.
    """
    if isinstance(raw_alert, dict):
        return raw_alert
    if isinstance(raw_alert, str):
        text = raw_alert.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# ---------------------------------------------------------------------------
# Anchor parsers — one per alert format
# ---------------------------------------------------------------------------
#
# Each parser inspects the raw alert payload and returns
# ``(anchor_datetime, source_label)`` if it can find a timestamp it
# trusts, else ``None``. The order tried in :func:`extract_anchor` reflects
# how reliable each format's timestamp is for incident-start time.


def _alertmanager_anchor(payload: dict[str, Any]) -> tuple[datetime, str] | None:
    """Alertmanager / Prometheus payloads carry ``startsAt`` per alert.

    Alertmanager wraps individual alerts in a top-level ``alerts`` list
    (webhook v4) and may also carry a top-level ``startsAt`` (older
    grouped alert payloads). ``startsAt`` is the moment the underlying
    condition began — strictly preferred over ``firedAt``, which is just
    when the rule sent the notification.

    When multiple alerts are present we use the EARLIEST ``startsAt`` so
    the resulting window covers the full firing burst rather than only
    the most recent alert.
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


# Grafana managed alerts use the Alertmanager webhook schema verbatim, so
# they are handled by ``_alertmanager_anchor`` above. No separate Grafana
# parser exists today; if Grafana ever adds a distinct timestamp field, add
# a dedicated parser here and register it in ``_ANCHOR_PARSERS``.


def _datadog_anchor(payload: dict[str, Any]) -> tuple[datetime, str] | None:
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


def _pagerduty_anchor(payload: dict[str, Any]) -> tuple[datetime, str] | None:
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


def _cloudwatch_anchor(payload: dict[str, Any], _depth: int = 0) -> tuple[datetime, str] | None:
    """CloudWatch alarm payloads carry ``StateUpdatedTimestamp``.

    The payload arrives wrapped in SNS, so the actual alarm dict often
    lives inside ``Message`` (which is itself a JSON string). We probe
    both top-level and nested shapes, including EventBridge ``alarmData``.

    Recursion is hard-capped at ``_CLOUDWATCH_MAX_DEPTH`` levels so a
    pathologically deep payload cannot blow the Python stack.
    """
    if _depth >= _CLOUDWATCH_MAX_DEPTH:
        return None
    # Top-level direct match.
    for key in ("StateUpdatedTimestamp", "stateUpdatedTimestamp"):
        value = payload.get(key)
        if isinstance(value, str):
            anchor = parse_iso8601(value)
            if anchor is not None:
                return anchor, SOURCE_ACTIVATED_AT
    # Nested probes.
    for nested_key in ("Message", "alarmData", "alarm"):
        nested = payload.get(nested_key)
        if isinstance(nested, str):
            try:
                nested = json.loads(nested)
            except json.JSONDecodeError:
                continue
        if isinstance(nested, dict):
            result = _cloudwatch_anchor(nested, _depth=_depth + 1)
            if result is not None:
                return result
    return None


# Order matters: the first parser to find an anchor wins. The order
# reflects which format expresses incident-start most accurately.
# Grafana managed alerts share Alertmanager's schema and are handled by
# ``_alertmanager_anchor`` — no separate parser is needed.
_AnchorParser = Callable[[dict[str, Any]], tuple[datetime, str] | None]
_ANCHOR_PARSERS: tuple[_AnchorParser, ...] = (
    _alertmanager_anchor,  # ``startsAt`` is the underlying condition time (also covers Grafana)
    _pagerduty_anchor,  # ``triggered_at`` is reliable for incident time
    _datadog_anchor,  # ``event_time`` is fired-at, less ideal
    _cloudwatch_anchor,  # ``StateUpdatedTimestamp`` is state-flip time
)


def extract_anchor(payload: dict[str, Any]) -> tuple[datetime, str] | None:
    """Try every parser. Return the first successful anchor.

    Each parser is wrapped in a try/except: a single misbehaving parser
    cannot prevent the others from running, and an upstream payload
    cannot crash the pipeline.
    """
    for parser in _ANCHOR_PARSERS:
        try:
            result = parser(payload)
        except Exception:
            logger.debug("incident_window: anchor parser %s raised", parser.__name__, exc_info=True)
            continue
        if result is not None:
            return result
    return None


__all__ = [
    "SOURCE_ACTIVATED_AT",
    "SOURCE_FIRED_AT",
    "SOURCE_STARTS_AT",
    "coerce_alert_dict",
    "extract_anchor",
    "parse_iso8601",
]
