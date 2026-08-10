"""Rootly Alerts: the signal that exists before anyone declares an incident.

Most of the time there is no incident — there is a firing alert moving through
triggered → acknowledged → resolved, with routing, dedup and grouping around
it. ``rootly_incidents`` only sees the ones a human escalated, which is the
window where an agent is *least* needed. This module covers the rest.

Two exposure surfaces make alerts riskier to shape than incidents, and both are
handled structurally rather than by filtering:

**The upstream payload.** ``Alert.data`` is documented by Rootly as
"Additional data" and is a free-form dict of whatever the monitoring system
POSTed — a Datadog webhook body, a Grafana annotation, an arbitrary
integration's JSON. It can contain credentials, customer records, anything.
Its **values are never emitted.** Only the field *names* are, so the model can
say "this alert carries a payload with these fields, open it in Rootly" rather
than either leaking it or pretending it does not exist.

**People, in three places.** ``responders`` (``UserFlatResponse``: required
``email``, plus ``phone`` and ``slack_id``) and ``notified_users`` (``User``:
required ``email``) are shaped by the allowlist in ``people.py``. Less
obviously, ``services`` / ``groups`` / ``environments`` / ``functionalities``
arrive as full objects, and ``Service`` carries ``notify_emails`` — so those
are reduced to bare names rather than passed through.

Paths and field names come from the official SDK, ``rootlyhq/rootly-python``;
``rootly.com/swagger`` is Cloudflare-gated and cannot be read from CI.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from integrations.rootly.jsonapi import attributes, named, truncate
from integrations.rootly.people import shape_people

ALERTS_PATH = "/v1/alerts"

# Rootly's AlertStatus enum. Surfaced to the model so it filters with a value
# the API accepts instead of guessing "firing" or "active".
ALERT_STATUSES: tuple[str, ...] = (
    "triggered",
    "open",
    "acknowledged",
    "resolved",
    "deferred",
)

# 403 means the account does not have Rootly Alerts. On the *collection* a 404
# means the same thing — the route is not mounted — but on a single alert it far
# more often means the id was wrong, so ``get_alert`` intercepts 404 before it
# reaches here and answers "no such alert" with no telemetry.
ALERTS_UNENTITLED_STATUSES = frozenset({HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND})
ALERT_GET_UNENTITLED_STATUSES = frozenset({HTTPStatus.FORBIDDEN})

_MAX_DESCRIPTION_CHARS = 1000
_MAX_PAYLOAD_FIELDS = 40

# ``summary`` is the alert headline and rides in the *list* shape, so a single
# call can carry 50 of them. It is monitor-supplied text from the same webhook
# body as ``data``, so it is bounded harder than the single-record
# ``description``: 50 unbounded strings is a prompt-size hazard on its own.
_MAX_SUMMARY_CHARS = 500


def alerts_entitlement_error(status: int) -> str:
    """Operator-readable message for an Alerts entitlement gap."""
    return (
        f"Rootly Alerts returned HTTP {status}. Rootly Alerts may not be enabled for this account, "
        "or this API key's user lacks alert permissions. Rootly incident tools are unaffected. Do not retry."
    )


def payload_field_names(value: Any) -> list[str]:
    """Field *names* from the free-form upstream payload — never the values.

    An alert body is whatever the monitoring vendor sent. Allowlisting keys is
    impossible because they differ per source, and truncating does not help: a
    truncated credential is still a credential. Names alone tell the model that
    more detail exists and where to go for it.
    """
    if not isinstance(value, dict):
        return []
    names = sorted(str(key) for key in value)
    return names[:_MAX_PAYLOAD_FIELDS]


def _names(value: Any) -> list[str]:
    """Reduce a list of Rootly objects to their names.

    Not cosmetic: ``Service`` carries ``notify_emails``, so passing these
    objects through would leak addresses the person allowlist never sees.
    """
    if not isinstance(value, list):
        return []
    resolved = [named(item) for item in value]
    return [name for name in resolved if name]


def _labels(value: Any) -> dict[str, Any]:
    """Flatten Rootly's ``[{key, value}]`` label list into a dict."""
    if not isinstance(value, list):
        return {}
    flattened: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if isinstance(key, str) and key:
            flattened[key] = item.get("value")
    return flattened


def shape_alert(record: Any, *, full: bool = False) -> dict[str, Any]:
    """Flatten one alert resource into the fields a responder actually reads."""
    attrs = attributes(record)
    shaped: dict[str, Any] = {
        "id": str(record.get("id", "")) if isinstance(record, dict) else "",
        "short_id": attrs.get("short_id", ""),
        "summary": truncate(str(attrs.get("summary") or ""), _MAX_SUMMARY_CHARS),
        "status": attrs.get("status", ""),
        "source": attrs.get("source", ""),
        "urgency": named(attrs.get("alert_urgency")),
        "noise": attrs.get("noise", ""),
        "services": _names(attrs.get("services")),
        "groups": _names(attrs.get("groups")),
        "environments": _names(attrs.get("environments")),
        "started_at": attrs.get("started_at", ""),
        "ended_at": attrs.get("ended_at", ""),
        "created_at": attrs.get("created_at", ""),
        "url": attrs.get("url", ""),
    }
    if full:
        shaped.update(
            {
                "description": truncate(
                    str(attrs.get("description") or ""), _MAX_DESCRIPTION_CHARS
                ),
                "functionalities": _names(attrs.get("functionalities")),
                "labels": _labels(attrs.get("labels")),
                "external_id": attrs.get("external_id", ""),
                "external_url": attrs.get("external_url", ""),
                "deduplication_key": attrs.get("deduplication_key", ""),
                "is_group_leader_alert": attrs.get("is_group_leader_alert", False),
                "group_leader_alert_id": attrs.get("group_leader_alert_id", ""),
                "responders": shape_people(attrs.get("responders")),
                "notified_users": shape_people(attrs.get("notified_users")),
                "alerting_targets": _alerting_targets(attrs.get("alerting_targets")),
                "payload_fields": payload_field_names(attrs.get("data")),
            }
        )
    return shaped


def _alerting_targets(value: Any) -> list[dict[str, str]]:
    """Who this alert would page, as ``{id, type}`` — no contact detail."""
    if not isinstance(value, list):
        return []
    return [
        {"id": str(item.get("id", "")), "type": str(item.get("type", ""))}
        for item in value
        if isinstance(item, dict)
    ]


__all__ = [
    "ALERTS_PATH",
    "ALERTS_UNENTITLED_STATUSES",
    "ALERT_GET_UNENTITLED_STATUSES",
    "ALERT_STATUSES",
    "alerts_entitlement_error",
    "payload_field_names",
    "shape_alert",
]
