"""incident.io incident context and summary write-back tool."""

from __future__ import annotations

from typing import Any

from app.services.incident_io import make_incident_io_client
from app.tools.base import BaseTool


class IncidentIoIncidentsTool(BaseTool):
    """Read incident.io incident context and optionally append OpenSRE findings."""

    name = "incident_io_incidents"
    source = "incident_io"
    description = (
        "Read incident.io incidents, incident metadata, and incident updates for RCA context. "
        "Can append OpenSRE findings to the incident summary through the supported edit endpoint."
    )
    use_cases = [
        "Listing live incident.io incidents related to the current alert",
        "Reading incident metadata, custom fields, roles, timestamps, and updates",
        "Using incident updates as timeline/status context during RCA",
        "Appending investigation findings to the incident summary when explicitly requested",
    ]
    requires = ["api_key"]
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "updates", "context", "append_summary"],
                "default": "context",
                "description": "Action to perform.",
            },
            "status_category": {
                "type": "string",
                "default": "live",
                "description": "Incident status category for list, e.g. live, triage, learning, or empty for all.",
            },
            "page_size": {
                "type": "integer",
                "default": 20,
                "description": "Maximum incidents or updates to return.",
            },
            "after": {
                "type": "string",
                "description": "Pagination cursor from incident.io.",
            },
            "incident_id": {
                "type": "string",
                "description": "incident.io incident ID for get, updates, context, or append_summary.",
            },
            "title": {
                "type": "string",
                "description": "Short title for append_summary.",
            },
            "body": {
                "type": "string",
                "description": "Detailed RCA findings or next steps for append_summary.",
            },
            "notify_incident_channel": {
                "type": "boolean",
                "default": False,
                "description": "Whether incident.io should notify the incident channel on summary update.",
            },
        },
        "required": [],
    }
    outputs = {
        "incidents": "List of incident summaries",
        "incident": "Full incident metadata for a single incident",
        "incident_updates": "Incident update timeline/status messages",
        "success": "Whether the action succeeded",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("incident_io", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        incident_io = sources.get("incident_io", {})
        incident_id = incident_io.get("incident_id", "")
        return {
            "api_key": incident_io.get("api_key", ""),
            "base_url": incident_io.get("base_url", ""),
            "action": "context" if incident_id else "list",
            "incident_id": incident_id,
            "status_category": incident_io.get("status_category", "live"),
            "page_size": incident_io.get("page_size", 20),
        }

    def run(
        self,
        api_key: str,
        *,
        region: str | None = None,
        base_url: str = "",
        action: str = "context",
        status_category: str = "live",
        page_size: int | None = 20,
        after: str | None = None,
        incident_id: str = "",
        title: str = "",
        body: str = "",
        notify_incident_channel: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_incident_io_client(api_key, region, base_url=base_url)
        if client is None:
            return {
                "source": "incident_io",
                "available": False,
                "success": False,
                "error": "incident.io integration is not configured.",
            }

        normalized_action = (action or "context").strip().lower()
        with client:
            if normalized_action == "list":
                result = client.list_incidents(
                    status_category=status_category,
                    page_size=page_size,
                    after=after,
                )
            elif normalized_action == "get":
                if not incident_id:
                    result = {"success": False, "error": "incident_id is required for get."}
                else:
                    result = client.get_incident(incident_id)
            elif normalized_action == "updates":
                if not incident_id:
                    result = {"success": False, "error": "incident_id is required for updates."}
                else:
                    result = client.list_incident_updates(
                        incident_id,
                        page_size=page_size,
                        after=after,
                    )
            elif normalized_action == "append_summary":
                if not incident_id:
                    result = {
                        "success": False,
                        "error": "incident_id is required for append_summary.",
                    }
                elif not title:
                    result = {"success": False, "error": "title is required for append_summary."}
                else:
                    result = client.append_summary_update(
                        incident_id,
                        title=title,
                        body=body,
                        notify_incident_channel=notify_incident_channel,
                    )
            else:
                if not incident_id:
                    result = {"success": False, "error": "incident_id is required for context."}
                else:
                    result = client.get_incident_context(incident_id, update_limit=page_size)

        result.update(
            {
                "source": "incident_io",
                "available": bool(result.get("success")),
                "action": normalized_action,
            }
        )
        return result


incident_io_incidents = IncidentIoIncidentsTool()
