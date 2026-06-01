"""PagerDuty incident detail and timeline investigation tool."""

from __future__ import annotations

from typing import Any

from app.services.pagerduty import make_pagerduty_client
from app.tools.base import BaseTool


class PagerDutyIncidentDetailTool(BaseTool):
    """Fetch full details and activity timeline for a specific PagerDuty incident."""

    name = "pagerduty_incident_detail"
    source = "pagerduty"
    description = (
        "Fetch the full details, assignments, acknowledgements, and activity timeline "
        "for a specific PagerDuty incident to understand its lifecycle and current state."
    )
    use_cases = [
        "Getting the full context of a PagerDuty incident during RCA",
        "Checking who acknowledged or was assigned to an incident",
        "Reviewing the incident timeline (escalations, annotations, status changes)",
        "Reading incident details (service, priority, teams) for correlation",
    ]
    requires = ["api_key", "incident_id"]
    injected_params = ["api_key", "base_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "PagerDuty REST API key"},
            "base_url": {
                "type": "string",
                "default": "https://api.pagerduty.com",
                "description": "PagerDuty API base URL",
            },
            "incident_id": {
                "type": "string",
                "description": "PagerDuty incident ID to fetch details for",
            },
            "include_log_entries": {
                "type": "boolean",
                "default": True,
                "description": "Whether to also fetch the incident timeline (log entries)",
            },
            "log_limit": {
                "type": "integer",
                "default": 25,
                "description": "Maximum number of log entries to fetch",
            },
        },
        "required": ["api_key", "incident_id"],
    }
    outputs = {
        "incident": "Full incident details including service, assignments, and priority",
        "log_entries": "Timeline entries showing escalations, acknowledgements, and annotations",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("pagerduty", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        pd = sources["pagerduty"]
        return {
            "api_key": pd.get("api_key", ""),
            "base_url": pd.get("base_url", ""),
            "incident_id": pd.get("incident_id", ""),
            "include_log_entries": True,
            "log_limit": 25,
        }

    def run(
        self,
        api_key: str,
        incident_id: str,
        base_url: str = "",
        include_log_entries: bool = True,
        log_limit: int = 25,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not incident_id:
            return {
                "source": "pagerduty",
                "available": False,
                "error": "incident_id is required. Run pagerduty_incidents first to find an ID.",
                "incident": {},
                "log_entries": [],
            }

        client = make_pagerduty_client(api_key, base_url or None)
        if client is None:
            return {
                "source": "pagerduty",
                "available": False,
                "error": "PagerDuty integration is not configured.",
                "incident": {},
                "log_entries": [],
            }

        with client:
            incident_result = client.get_incident(incident_id)
            incident = incident_result.get("incident", {}) if incident_result.get("success") else {}

            log_entries: list[dict[str, Any]] = []
            if incident_result.get("success") and include_log_entries:
                logs_result = client.list_incident_log_entries(incident_id, limit=log_limit)
                if logs_result.get("success"):
                    log_entries = logs_result.get("log_entries", [])

        if not incident_result.get("success"):
            return {
                "source": "pagerduty",
                "available": False,
                "error": incident_result.get("error", "unknown error"),
                "incident": {},
                "log_entries": [],
            }

        return {
            "source": "pagerduty",
            "available": True,
            "incident_id": incident_id,
            "incident": incident,
            "log_entries": log_entries,
            "total_log_entries": len(log_entries),
        }


pagerduty_incident_detail = PagerDutyIncidentDetailTool()
