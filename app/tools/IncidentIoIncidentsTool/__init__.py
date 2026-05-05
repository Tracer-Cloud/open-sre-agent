"""Incident.io incident listing and timeline update tool."""

from __future__ import annotations

from typing import Any

from app.services.incident_io.client import make_incident_io_client
from app.tools.base import BaseTool


class IncidentIoIncidentsTool(BaseTool):
    """List incident.io incidents or add updates to a specific incident."""

    name = "incident_io_incidents"
    source = "incident_io"
    description = (
        "Interact with incident.io to list active incidents or post investigation timeline updates. "
        "Provide action='list' to search for incidents (e.g. status=open) or action='add_timeline' "
        "to add a new timeline event (requires incident_id and comment)."
    )
    use_cases = [
        "Listing open incidents to understand current context",
        "Finding an incident by status",
        "Adding a timeline event with RCA findings to an ongoing incident",
    ]
    requires = ["api_key"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "Incident.io API key"},
            "action": {
                "type": "string",
                "enum": ["list", "add_timeline", "get"],
                "default": "list",
                "description": "Action to perform: list, add_timeline, or get",
            },
            "status": {
                "type": "string",
                "default": "open",
                "description": "Incident status filter for 'list' action (e.g. open, closed, or empty for all)",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of incidents to return per page (for 'list' action)",
            },
            "after": {
                "type": "string",
                "description": "Pagination cursor to fetch the next page of incidents (for 'list' action)",
            },
            "incident_id": {
                "type": "string",
                "description": "Incident ID required for 'add_timeline' or 'get' actions",
            },
            "title": {
                "type": "string",
                "description": "Short title for the timeline event (required for 'add_timeline')",
            },
            "comment": {
                "type": "string",
                "description": "Detailed description/comment for the timeline event (used in 'add_timeline')",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "incidents": "List of incidents or details of a specific incident",
        "total": "Total number of incidents returned",
        "success": "Whether the action was successful",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("incident_io", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        integration = sources.get("incident_io", {})
        return {
            "api_key": integration.get("api_key", ""),
        }

    def run(
        self,
        api_key: str,
        action: str = "list",
        status: str = "open",
        incident_id: str = "",
        title: str = "",
        comment: str = "",
        page_size: int | None = None,
        after: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_incident_io_client(api_key)
        if client is None:
            return {
                "source": "incident_io",
                "available": False,
                "error": "Incident.io integration is not configured.",
                "success": False,
            }

        with client:
            if action == "add_timeline":
                if not incident_id:
                    return {"success": False, "error": "incident_id is required for add_timeline"}
                if not title:
                    return {"success": False, "error": "title is required for add_timeline"}

                result = client.add_timeline_event(incident_id, title=title, description=comment)
                result["action"] = action
                return result

            elif action == "get":
                if not incident_id:
                    return {"success": False, "error": "incident_id is required for get"}
                result = client.get_incident(incident_id)
                result["action"] = action
                return result

            else:
                # default action == "list"
                result = client.list_incidents(status=status, page_size=page_size, after=after)
                result["action"] = action
                return result


incident_io_incidents = IncidentIoIncidentsTool()
