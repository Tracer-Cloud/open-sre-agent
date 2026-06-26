"""PagerDuty incident listing and search investigation tool."""

from __future__ import annotations

from typing import Any

from services.pagerduty import make_pagerduty_client
from tools.base import BaseTool

_ACTIVE_STATUSES = {"triggered", "acknowledged"}


class PagerDutyIncidentsTool(BaseTool):
    """List and search PagerDuty incidents to surface active pages and their triage state."""

    name = "pagerduty_incidents"
    source = "pagerduty"
    description = (
        "Search PagerDuty incidents to find active pages, identify unacknowledged triggered "
        "incidents, and correlate incident context with infrastructure events during RCA."
    )
    use_cases = [
        "Listing active PagerDuty incidents for an ongoing investigation",
        "Finding unacknowledged triggered incidents",
        "Correlating a PagerDuty incident with errors in Datadog or Sentry",
        "Checking recent incident history for a service",
    ]
    requires = ["api_key"]
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
            "statuses": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by status: triggered, acknowledged, resolved",
            },
            "urgencies": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by urgency: high, low",
            },
            "service_ids": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by PagerDuty service IDs",
            },
            "since": {
                "type": "string",
                "default": "",
                "description": "Start of date range (ISO 8601, e.g. 2024-01-01T00:00:00Z)",
            },
            "until": {
                "type": "string",
                "default": "",
                "description": "End of date range (ISO 8601)",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Maximum number of incidents to return",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "incidents": "List of incidents with status, urgency, service, and timestamps",
        "active_incidents": "Subset of incidents in triggered or acknowledged state",
        "total": "Total number of incidents returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("pagerduty", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        pd = sources["pagerduty"]
        return {
            "api_key": pd.get("api_key", ""),
            "base_url": pd.get("base_url", ""),
            "statuses": [],
            "urgencies": [],
            "service_ids": [],
            "since": "",
            "until": "",
            "limit": 25,
        }

    def run(
        self,
        api_key: str,
        base_url: str = "",
        statuses: list[str] | None = None,
        urgencies: list[str] | None = None,
        service_ids: list[str] | None = None,
        since: str = "",
        until: str = "",
        limit: int = 25,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_pagerduty_client(api_key, base_url or None)
        if client is None:
            return {
                "source": "pagerduty",
                "available": False,
                "error": "PagerDuty integration is not configured.",
                "incidents": [],
                "active_incidents": [],
                "total": 0,
            }

        with client:
            result = client.list_incidents(
                statuses=statuses or None,
                urgencies=urgencies or None,
                service_ids=service_ids or None,
                since=since or None,
                until=until or None,
                limit=limit,
            )

        if not result.get("success"):
            return {
                "source": "pagerduty",
                "available": False,
                "error": result.get("error", "unknown error"),
                "incidents": [],
                "active_incidents": [],
                "total": 0,
            }

        incidents = result.get("incidents", [])
        active_incidents = [i for i in incidents if i.get("status", "").lower() in _ACTIVE_STATUSES]
        return {
            "source": "pagerduty",
            "available": True,
            "incidents": incidents,
            "active_incidents": active_incidents,
            "total": len(incidents),
        }


pagerduty_incidents = PagerDutyIncidentsTool()
