"""Write-back: record a finding on a Rootly incident timeline.

This is the half of the Rootly integration that makes the rest worth having.
Reading an incident tells the responder nothing they cannot see in Rootly; the
value is the agent's RCA landing *on the incident*, where the retrospective
will find it.

Gated twice. ``requires_approval`` puts Approve / Deny in front of every call,
and the write is append-only — it can add an event to a timeline, never edit
one, never change an incident's status, and never resolve it. The worst an
approved-by-mistake call produces is a note somebody has to read.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.rootly.client import normalize_visibility
from integrations.rootly.tools.credentials import (
    ROOTLY_INJECTED_PARAMS,
    SOURCE,
    resolve_client,
    rootly_available,
    rootly_creds,
)
from integrations.rootly.tools.timeline_approval import TIMELINE_EVENT_APPROVAL_DISPLAY

_DESCRIPTION = (
    "Append one event to a Rootly incident's timeline — a finding, a ruled-out "
    "hypothesis, or a next step. Appends only: it cannot edit an existing event, "
    "change the incident status, or resolve the incident. Each call needs human "
    "approval before anything is written."
)

_APPROVAL_REASON = "Writes a note to a Rootly incident timeline that responders will read."

_USE_CASE_FINDING = "Recording an investigation's root-cause finding on the incident it belongs to"
_USE_CASE_RULED_OUT = (
    "Noting a hypothesis that was checked and ruled out, so nobody re-investigates it"
)
_ANTI_EXAMPLE_STATUS = (
    "Resolving or changing the status of an incident (this tool only adds a timeline note)"
)
_ANTI_EXAMPLE_CHATTER = (
    "Posting routine progress chatter — the timeline is the incident record, not a log stream"
)
_ANTI_EXAMPLE_EXTERNAL = (
    "Choosing external visibility unless the user explicitly asked to publish to customers"
)

_EVENT_DESCRIPTION = (
    "The note to record, as complete prose. Rootly stamps the time on creation, "
    "so do not prefix a timestamp."
)
_VISIBILITY_DESCRIPTION = (
    "internal (default) shows the note to responders only; external publishes it to the "
    "customer-facing status timeline. Use external only when the user explicitly asks."
)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    record = sources.get(SOURCE, {})
    return {"incident_id": record.get("incident_id", ""), **rootly_creds(sources)}


@tool(
    name="rootly_post_timeline_event",
    source=SOURCE,
    description=_DESCRIPTION,
    use_cases=[
        _USE_CASE_FINDING,
        _USE_CASE_RULED_OUT,
        "Leaving the remediation the agent proposed on the incident for a responder to action",
    ],
    anti_examples=[
        _ANTI_EXAMPLE_STATUS,
        _ANTI_EXAMPLE_CHATTER,
        _ANTI_EXAMPLE_EXTERNAL,
    ],
    requires=["incident_id", "event"],
    surfaces=("action", "investigation"),
    side_effect_level="mutating",
    requires_approval=True,
    approval_reason=_APPROVAL_REASON,
    # Renders the prompt as the note it is about to publish, leading with
    # visibility; see integrations/rootly/tools/timeline_approval.py.
    approval_display=TIMELINE_EVENT_APPROVAL_DISPLAY,
    input_schema={
        "type": "object",
        "properties": {
            "incident_id": {
                "type": "string",
                "description": "Rootly incident id or slug to append to.",
            },
            "event": {"type": "string", "description": _EVENT_DESCRIPTION},
            "visibility": {
                "type": "string",
                "enum": ["internal", "external"],
                "default": "internal",
                "description": _VISIBILITY_DESCRIPTION,
            },
        },
        "required": ["incident_id", "event"],
    },
    outputs={
        "event_id": "Identifier Rootly assigned to the created event",
        "visibility": "Visibility the event was created with",
    },
    is_available=rootly_available,
    extract_params=_extract_params,
    injected_params=ROOTLY_INJECTED_PARAMS,
)
def rootly_post_timeline_event(
    incident_id: str,
    event: str,
    visibility: str = "internal",
    rootly_token: str | None = None,
    rootly_base_url: str | None = None,
    rootly_timeout: float | str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Append *event* to the timeline of *incident_id*."""
    client = resolve_client(rootly_token, rootly_base_url, rootly_timeout)
    if client is None:
        return tool_unavailable(SOURCE, "Rootly integration is not configured.", event_id="")
    if not incident_id.strip():
        return tool_unavailable(SOURCE, "incident_id is required.", event_id="")

    with client:
        result = client.post_timeline_event(
            incident_id.strip(),
            event=event,
            visibility=normalize_visibility(visibility),
        )

    if not result.get("success"):
        return tool_unavailable(SOURCE, str(result.get("error", "unknown error")), event_id="")
    return {"source": SOURCE, "available": True, **result}
