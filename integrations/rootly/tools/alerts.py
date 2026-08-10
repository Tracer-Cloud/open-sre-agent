"""Read-only Rootly alerts: the firing signal before anyone declares an incident.

Most of the time there is no incident — there is an alert moving through
triggered → acknowledged → resolved. ``rootly_incidents`` only sees the ones a
human escalated, so an agent limited to incidents is blind during exactly the
window where it is most useful.

Two reads share one schema behind an ``action`` selector, matching
``rootly_incidents`` and ``rootly_on_call``.

**No writes, deliberately.** Acknowledging or resolving an alert would be
defensible behind an approval gate, but *paging a human* would not: the failure
mode is somebody's sleep, the value is low (a responder already in Slack pages
in one click), and an approval prompt does not make a mis-page at 3am
recoverable. Nothing in this module can change state in Rootly.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.rootly.alerts import ALERT_STATUSES
from integrations.rootly.tools.credentials import (
    ROOTLY_INJECTED_PARAMS,
    SOURCE,
    resolve_client,
    rootly_available,
    rootly_creds,
)

_ACTION_LIST = "list"
_ACTION_GET = "get"

_DESCRIPTION = (
    "Read Rootly alerts: list firing or recent alerts, or fetch one alert in full "
    "with its responders, labels, and routing. Alerts are the pre-incident signal — "
    "use this when nothing has been declared yet. Read-only."
)

# Prose over the line limit lives in module constants: two adjacent string
# literals inside a list are indistinguishable from a missing comma.
_USE_CASE_FIRING = (
    "Finding what is currently firing when no incident has been declared for the symptom yet"
)
_USE_CASE_CORRELATE = (
    "Checking whether the service under investigation has alerts open on it right now"
)
_USE_CASE_HISTORY = (
    "Reading recent alerts on a service to see whether this symptom has fired before"
)
_USE_CASE_ROUTING = "Fetching one alert to see which services, groups, and responders it routed to"

_ANTI_EXAMPLE_WRITE = (
    "Acknowledging, resolving, or snoozing an alert — this tool only reads, and paging is "
    "deliberately not available to the agent at all"
)
_ANTI_EXAMPLE_INCIDENT = (
    "Reading a declared incident or its timeline — use rootly_incidents for anything escalated"
)

_ALERT_ID_DESCRIPTION = (
    "Rootly alert id. Required for the get action; take it from a rootly_alerts list result."
)
_STATUS_DESCRIPTION = (
    "Filter the list by alert status. Omit for all. Use triggered or open for what is "
    "firing right now."
)
_SOURCE_DESCRIPTION = (
    "Filter by the upstream system that raised the alert, e.g. datadog, grafana, webhook."
)
_SERVICE_DESCRIPTION = (
    "Filter by Rootly service name or slug. Take it from the services field of a previous result."
)
_ENVIRONMENT_DESCRIPTION = "Filter by Rootly environment name or slug, e.g. production."
_STARTED_AFTER_DESCRIPTION = (
    "Only list alerts that started at or after this ISO-8601 timestamp, e.g. 2026-08-01T00:00:00Z."
)

# The alert body is whatever the upstream monitor POSTed, so only its field
# names are returned. Saying so in the schema stops the model from reporting
# an empty payload as "no data" or asking for the values in a follow-up call.
_PAYLOAD_FIELDS_OUTPUT = (
    "alert.payload_fields: names of the fields in the raw upstream payload. Values are "
    "withheld — open the alert url in Rootly to read them (get action)"
)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject credentials and default to listing what is firing."""
    return {
        "action": _ACTION_LIST,
        **rootly_creds(sources),
    }


@tool(
    name="rootly_alerts",
    source=SOURCE,
    description=_DESCRIPTION,
    use_cases=[
        _USE_CASE_FIRING,
        _USE_CASE_CORRELATE,
        _USE_CASE_HISTORY,
        _USE_CASE_ROUTING,
    ],
    anti_examples=[
        _ANTI_EXAMPLE_WRITE,
        _ANTI_EXAMPLE_INCIDENT,
    ],
    surfaces=("action", "investigation"),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [_ACTION_LIST, _ACTION_GET],
                "default": _ACTION_LIST,
                "description": "Which read to perform.",
            },
            "alert_id": {"type": "string", "description": _ALERT_ID_DESCRIPTION},
            "status": {
                "type": "string",
                "enum": list(ALERT_STATUSES),
                "description": _STATUS_DESCRIPTION,
            },
            "alert_source": {"type": "string", "description": _SOURCE_DESCRIPTION},
            "service": {"type": "string", "description": _SERVICE_DESCRIPTION},
            "environment": {"type": "string", "description": _ENVIRONMENT_DESCRIPTION},
            "started_after": {"type": "string", "description": _STARTED_AFTER_DESCRIPTION},
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum alerts to return.",
            },
        },
        "required": [],
    },
    outputs={
        "alerts": "Alert summaries, newest first, with status, source, and services (list action)",
        "alert": "One alert with labels, routing, and shaped responders (get action)",
        "payload_fields": _PAYLOAD_FIELDS_OUTPUT,
        "truncated": "Whether Rootly holds more alerts than were returned",
    },
    is_available=rootly_available,
    extract_params=_extract_params,
    injected_params=ROOTLY_INJECTED_PARAMS,
)
def rootly_alerts(
    action: str = _ACTION_LIST,
    alert_id: str = "",
    status: str = "",
    alert_source: str = "",
    service: str = "",
    environment: str = "",
    started_after: str = "",
    limit: int = 20,
    rootly_token: str | None = None,
    rootly_base_url: str | None = None,
    rootly_timeout: float | str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Read Rootly alerts: the firing list, or one alert in full."""
    client = resolve_client(rootly_token, rootly_base_url, rootly_timeout)
    if client is None:
        return tool_unavailable(SOURCE, "Rootly integration is not configured.", alerts=[])

    normalized = (action or _ACTION_LIST).strip().lower()
    if normalized == _ACTION_GET and not alert_id:
        return tool_unavailable(SOURCE, "alert_id is required for the get action.", alerts=[])

    with client:
        if normalized == _ACTION_GET:
            result = client.get_alert(alert_id)
        else:
            normalized = _ACTION_LIST
            result = client.list_alerts(
                status=status,
                source=alert_source,
                service=service,
                environment=environment,
                started_after=started_after,
                page_size=limit,
            )

    if not result.get("success"):
        # entitled is False only when Rootly Alerts is not on this account;
        # every other failure keeps the default so the model retries sensibly.
        entitled = result.get("entitled", True)
        return tool_unavailable(
            SOURCE, str(result.get("error", "unknown error")), alerts=[], entitled=entitled
        )
    return {"source": SOURCE, "available": True, "action": normalized, **result}
