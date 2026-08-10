"""Read-only Rootly on-call lookup: who is on call, schedules, and escalation policies.

Three reads share one schema behind an ``action`` selector rather than shipping
three tools. Every tool schema is sent to the model on every turn, so the choice
is between one enum field and two extra schemas. Precedent: ``rootly_incidents``.

On-call lookup is read-only and never requires approval. PII protection is
structural: person records expose only id, name, name_with_team, and time_zone.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.rootly.tools.credentials import (
    ROOTLY_INJECTED_PARAMS,
    SOURCE,
    resolve_client,
    rootly_available,
    rootly_creds,
)

_ACTION_WHO_IS_ON_CALL = "who_is_on_call"
_ACTION_SCHEDULES = "schedules"
_ACTION_ESCALATION_POLICIES = "escalation_policies"

_DESCRIPTION = (
    "Read Rootly on-call information: who is on call now or at a specific time, "
    "on-call schedules with Slack groups, or escalation policies. Read-only."
)

# Long prose goes in module constants to avoid implicit string concatenation
_USE_CASE_CURRENT = "Checking who is currently on call for a specific service or escalation policy"
_USE_CASE_COVERAGE = (
    "Viewing upcoming on-call coverage to see who will be on call during a maintenance window"
)
_USE_CASE_SCHEDULES = (
    "Finding the right Slack user group or channel to notify for an escalation policy"
)

_ANTI_EXAMPLE_MODIFY = "Changing on-call schedules or escalation policies (this tool only reads)"
_ANTI_EXAMPLE_OVERRIDE = "Creating schedule overrides or shadow shifts"

_SCHEDULE_ID_DESCRIPTION = (
    "Restrict who_is_on_call to one schedule. Take the id from a schedules result."
)
_ESCALATION_POLICY_ID_DESCRIPTION = "Restrict who_is_on_call to one escalation policy. Take the id from an escalation_policies result."
_SINCE_DESCRIPTION = (
    "Start of the coverage window as an ISO-8601 timestamp, e.g. 2026-08-07T22:00:00Z. "
    "Omit for who is on call right now."
)
_UNTIL_DESCRIPTION = (
    "End of the coverage window as an ISO-8601 timestamp. Use with since to see upcoming coverage."
)
_SEARCH_DESCRIPTION = (
    "Free-text match on schedule or escalation-policy name. Ignored by who_is_on_call."
)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject credentials and default to who_is_on_call."""
    return {
        "action": _ACTION_WHO_IS_ON_CALL,
        **rootly_creds(sources),
    }


@tool(
    name="rootly_on_call",
    source=SOURCE,
    description=_DESCRIPTION,
    use_cases=[
        _USE_CASE_CURRENT,
        "Finding out who to contact for an urgent production issue",
        _USE_CASE_COVERAGE,
        _USE_CASE_SCHEDULES,
    ],
    anti_examples=[
        _ANTI_EXAMPLE_MODIFY,
        _ANTI_EXAMPLE_OVERRIDE,
    ],
    surfaces=("action", "investigation"),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [_ACTION_WHO_IS_ON_CALL, _ACTION_SCHEDULES, _ACTION_ESCALATION_POLICIES],
                "default": _ACTION_WHO_IS_ON_CALL,
                "description": "Which read to perform.",
            },
            "schedule_id": {"type": "string", "description": _SCHEDULE_ID_DESCRIPTION},
            "escalation_policy_id": {
                "type": "string",
                "description": _ESCALATION_POLICY_ID_DESCRIPTION,
            },
            "since": {"type": "string", "description": _SINCE_DESCRIPTION},
            "until": {"type": "string", "description": _UNTIL_DESCRIPTION},
            "search": {"type": "string", "description": _SEARCH_DESCRIPTION},
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum records to return.",
            },
        },
        "required": [],
    },
    outputs={
        "on_call": "Current on-call assignments with person details (who_is_on_call action)",
        "schedules": "On-call schedules with Slack channels and user groups (schedules action)",
        "escalation_policies": "Escalation policies with repeat counts and coverage (escalation_policies action)",
        "truncated": "Whether Rootly holds more records than were returned",
    },
    is_available=rootly_available,
    extract_params=_extract_params,
    injected_params=ROOTLY_INJECTED_PARAMS,
)
def rootly_on_call(
    action: str = _ACTION_WHO_IS_ON_CALL,
    schedule_id: str = "",
    escalation_policy_id: str = "",
    since: str = "",
    until: str = "",
    search: str = "",
    limit: int = 20,
    rootly_token: str | None = None,
    rootly_base_url: str | None = None,
    rootly_timeout: float | str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Read Rootly on-call information: who is on call, schedules, or escalation policies."""
    client = resolve_client(rootly_token, rootly_base_url, rootly_timeout)
    if client is None:
        return tool_unavailable(SOURCE, "Rootly integration is not configured.", on_call=[])

    normalized = (action or _ACTION_WHO_IS_ON_CALL).strip().lower()

    with client:
        if normalized == _ACTION_SCHEDULES:
            result = client.list_on_call_schedules(search=search, limit=limit)
        elif normalized == _ACTION_ESCALATION_POLICIES:
            result = client.list_escalation_policies(search=search, limit=limit)
        else:
            normalized = _ACTION_WHO_IS_ON_CALL
            result = client.list_on_call(
                schedule_id=schedule_id,
                escalation_policy_id=escalation_policy_id,
                since=since,
                until=until,
                limit=limit,
            )

    if not result.get("success"):
        entitled = result.get("entitled", True)  # Only False for on-call entitlement gaps
        return tool_unavailable(
            SOURCE, str(result.get("error", "unknown error")), on_call=[], entitled=entitled
        )

    return {"source": SOURCE, "available": True, "action": normalized, **result}
