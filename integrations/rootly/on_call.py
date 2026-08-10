"""On-call specific data shaping and entitlement error handling.

Rootly On-Call is a separate product from Rootly Incidents. This module handles
the On-Call API paths, response shaping, and entitlement degradation when an
account lacks the On-Call product.

Person records go through the shared PII allowlist in ``people.py`` — email
addresses, phone numbers, and timestamps are structurally dropped, never
emitted as keys, regardless of what Rootly includes.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from integrations.rootly.jsonapi import attributes, named
from integrations.rootly.people import shape_person

# API paths
ON_CALL_PATH = "/v1/oncalls"
SCHEDULES_PATH = "/v1/schedules"
ESCALATION_POLICIES_PATH = "/v1/escalation_policies"

# Rootly answers 403 when the account lacks the On-Call product and 404 when the
# key's user cannot see the resource. The operator's next step is the same for
# both, so they collapse to one message. 401 is deliberately absent: a revoked
# token needs re-running setup, not buying a product.
ON_CALL_UNENTITLED_STATUSES = frozenset({HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND})


def on_call_entitlement_error(status: int) -> str:
    """Operator-readable message for on-call entitlement gaps."""
    return (
        f"Rootly On-Call returned HTTP {status}. Rootly On-Call may not be enabled for this account, "
        "or this API key's user lacks on-call permissions. Rootly incident tools are unaffected. Do not retry."
    )


def index_included_users(payload: Any) -> dict[str, dict[str, Any]]:
    """Build a {str(id): attributes} index from included user records.

    Oncall.user_id is int; included user id is str. Key on str(user_id) to join.
    """
    if not isinstance(payload, dict):
        return {}
    included = payload.get("included", [])
    if not isinstance(included, list):
        return {}

    users: dict[str, dict[str, Any]] = {}
    for record in included:
        if not isinstance(record, dict):
            continue
        if record.get("type") != "users":
            continue
        user_id = record.get("id")
        if not isinstance(user_id, str):
            continue
        users[user_id] = attributes(record)
    return users


def shape_on_call(record: Any, users: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Shape one on-call record, joining with user data for the person field."""
    attrs = attributes(record)
    user_id = attrs.get("user_id")

    # Join with user data
    person_attrs = users.get(str(user_id), {}) if isinstance(user_id, int) else {}
    person = shape_person(user_id, person_attrs) if isinstance(user_id, int) else {}

    return {
        "id": str(record.get("id", "")) if isinstance(record, dict) else "",
        "person": person,
        "escalation_policy": attrs.get("escalation_policy_name", ""),
        "escalation_policy_id": attrs.get("escalation_policy_id", ""),
        "escalation_level": attrs.get("escalation_level", 0),
        "escalation_path": attrs.get("escalation_policy_path_name", ""),
        "schedule": attrs.get("schedule_name", ""),
        "schedule_id": attrs.get("schedule_id", ""),
        "notification_type": attrs.get("notification_type", ""),
        "starts_at": attrs.get("starts_at", ""),
        "ends_at": attrs.get("ends_at", ""),
    }


def shape_schedule(record: Any) -> dict[str, Any]:
    """Shape one schedule record, flattening Slack references."""
    attrs = attributes(record)

    return {
        "id": str(record.get("id", "")) if isinstance(record, dict) else "",
        "name": attrs.get("name", ""),
        "description": attrs.get("description", ""),
        "all_time_coverage": attrs.get("all_time_coverage", False),
        "slack_channel": named(attrs.get("slack_channel")),
        "slack_user_group": named(attrs.get("slack_user_group")),
        "owner_group_ids": attrs.get("owner_group_ids", []),
    }


def shape_escalation_policy(record: Any) -> dict[str, Any]:
    """Shape one escalation policy record."""
    attrs = attributes(record)

    return {
        "id": str(record.get("id", "")) if isinstance(record, dict) else "",
        "name": attrs.get("name", ""),
        "description": attrs.get("description", ""),
        "repeat_count": attrs.get("repeat_count", 0),
        "group_ids": attrs.get("group_ids", []),
        "service_ids": attrs.get("service_ids", []),
    }
