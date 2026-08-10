"""Read-only Rootly incident context: the list, one incident, and its timeline.

Three reads share one schema behind an ``action`` selector rather than shipping
three tools. Every tool schema is sent to the model on every turn, so the choice
is between one enum field and two extra schemas — and the three actions differ
only in which identifier they need. Precedent: ``incident_io_incidents``.

The write path is deliberately *not* here: it needs human approval, and
approval is declared per tool, so folding it in would gate the reads too.
See ``timeline.py``.
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

_ACTION_LIST = "list"
_ACTION_GET = "get"
_ACTION_EVENTS = "events"

_DESCRIPTION = (
    "Read Rootly incidents: list open or recent incidents, fetch one incident's "
    "details, or read an incident's timeline of events. Read-only."
)

# Prose that exceeds the line limit goes in a module constant — two adjacent
# string literals inside a list are indistinguishable from a missing comma.
_USE_CASE_CORRELATE = (
    "Checking whether the alert under investigation already has a Rootly incident open"
)
_USE_CASE_TIMELINE = (
    "Reading an incident's timeline to see what responders have already tried and ruled out"
)
_ANTI_EXAMPLE_DECLARE = (
    "Declaring, resolving, or changing the status of an incident (this tool only reads)"
)
_ANTI_EXAMPLE_WRITE = (
    "Recording a finding on the timeline — use rootly_post_timeline_event, which asks first"
)

_INCIDENT_ID_DESCRIPTION = (
    "Rootly incident id or slug. Required for the get and events actions; "
    "take it from a rootly_incidents list result."
)
_STATUS_DESCRIPTION = (
    "Filter the list by Rootly status, e.g. started, mitigated, resolved. Omit for all."
)
_CREATED_AFTER_DESCRIPTION = (
    "Only list incidents created at or after this ISO-8601 timestamp, e.g. 2026-08-01T00:00:00Z."
)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject credentials, and default to whichever action the alert supports."""
    record = sources.get(SOURCE, {})
    incident_id = record.get("incident_id", "")
    return {
        "action": _ACTION_GET if incident_id else _ACTION_LIST,
        "incident_id": incident_id,
        **rootly_creds(sources),
    }


@tool(
    name="rootly_incidents",
    source=SOURCE,
    description=_DESCRIPTION,
    use_cases=[
        _USE_CASE_CORRELATE,
        "Listing the incidents currently open so the responder knows what is in flight",
        _USE_CASE_TIMELINE,
    ],
    anti_examples=[
        _ANTI_EXAMPLE_DECLARE,
        _ANTI_EXAMPLE_WRITE,
    ],
    surfaces=("action", "investigation"),
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [_ACTION_LIST, _ACTION_GET, _ACTION_EVENTS],
                "default": _ACTION_LIST,
                "description": "Which read to perform.",
            },
            "incident_id": {"type": "string", "description": _INCIDENT_ID_DESCRIPTION},
            "status": {"type": "string", "description": _STATUS_DESCRIPTION},
            "severity": {
                "type": "string",
                "description": "Filter the list by severity name, e.g. sev1. Omit for all.",
            },
            "created_after": {"type": "string", "description": _CREATED_AFTER_DESCRIPTION},
            "search": {
                "type": "string",
                "description": "Free-text search across incident titles and summaries.",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum incidents or events to return.",
            },
        },
        "required": [],
    },
    outputs={
        "incidents": "Incident summaries, newest first (list action)",
        "incident": "One incident with summary, severity, status, and timestamps (get action)",
        "events": "Timeline events, oldest first (events action)",
        "truncated": "Whether Rootly holds more records than were returned",
    },
    is_available=rootly_available,
    extract_params=_extract_params,
    injected_params=ROOTLY_INJECTED_PARAMS,
)
def rootly_incidents(
    action: str = _ACTION_LIST,
    incident_id: str = "",
    status: str = "",
    severity: str = "",
    created_after: str = "",
    search: str = "",
    limit: int = 20,
    rootly_token: str | None = None,
    rootly_base_url: str | None = None,
    rootly_timeout: float | str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Read Rootly incidents, one incident, or an incident timeline."""
    client = resolve_client(rootly_token, rootly_base_url, rootly_timeout)
    if client is None:
        return tool_unavailable(SOURCE, "Rootly integration is not configured.", incidents=[])

    normalized = (action or _ACTION_LIST).strip().lower()
    if normalized in {_ACTION_GET, _ACTION_EVENTS} and not incident_id:
        return tool_unavailable(
            SOURCE, f"incident_id is required for the {normalized} action.", incidents=[]
        )

    with client:
        if normalized == _ACTION_GET:
            result = client.get_incident(incident_id)
        elif normalized == _ACTION_EVENTS:
            result = client.list_incident_events(incident_id, page_size=limit)
        else:
            normalized = _ACTION_LIST
            result = client.list_incidents(
                status=status,
                severity=severity,
                created_after=created_after,
                search=search,
                page_size=limit,
            )

    if not result.get("success"):
        return tool_unavailable(SOURCE, str(result.get("error", "unknown error")), incidents=[])
    return {"source": SOURCE, "available": True, "action": normalized, **result}
