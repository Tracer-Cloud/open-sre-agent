# ======== from tools/opsgenie_alert_detail_tool/ ========

"""OpsGenie alert detail and activity log investigation tool."""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool
from vendors.opsgenie.client import make_opsgenie_client


class OpsGenieAlertDetailTool(BaseTool):
    """Fetch full details and activity log for a specific OpsGenie alert."""

    name = "opsgenie_alert_detail"
    source = "opsgenie"
    description = (
        "Fetch the full details, description, responder info, and activity log for a specific "
        "OpsGenie alert to understand its lifecycle and current triage state."
    )
    use_cases = [
        "Getting the full description and context of an OpsGenie alert",
        "Checking who acknowledged or responded to an alert",
        "Reviewing the activity timeline for an alert during an incident",
        "Reading alert details (custom fields, tags, entity) for RCA context",
    ]
    requires = ["api_key", "alert_id"]
    injected_params = ["api_key"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "OpsGenie API key (GenieKey)"},
            "region": {
                "type": "string",
                "default": "us",
                "description": "OpsGenie region: us or eu",
            },
            "alert_id": {
                "type": "string",
                "description": "OpsGenie alert ID to fetch details for",
            },
            "include_activity_log": {
                "type": "boolean",
                "default": True,
                "description": "Whether to also fetch the alert activity log",
            },
            "log_limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum number of activity log entries to fetch",
            },
        },
        "required": ["api_key", "alert_id"],
    }
    outputs = {
        "alert": "Full alert details including description, responders, tags, and details",
        "activity_log": "Activity log entries showing alert lifecycle events",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("opsgenie", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        og = sources["opsgenie"]
        return {
            "api_key": og.get("api_key", ""),
            "region": og.get("region", "us"),
            "alert_id": og.get("alert_id", ""),
            "include_activity_log": True,
            "log_limit": 20,
        }

    def run(
        self,
        api_key: str,
        alert_id: str,
        region: str = "us",
        include_activity_log: bool = True,
        log_limit: int = 20,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not alert_id:
            return {
                "source": "opsgenie",
                "available": False,
                "error": "alert_id is required. Run opsgenie_alerts first to find an alert ID.",
                "alert": {},
                "activity_log": [],
            }

        client = make_opsgenie_client(api_key, region)
        if client is None:
            return {
                "source": "opsgenie",
                "available": False,
                "error": "OpsGenie integration is not configured.",
                "alert": {},
                "activity_log": [],
            }

        with client:
            alert_result = client.get_alert(alert_id)
            alert = alert_result.get("alert", {}) if alert_result.get("success") else {}

            activity_log: list[dict[str, Any]] = []
            if alert_result.get("success") and include_activity_log:
                logs_result = client.get_alert_logs(alert_id, limit=log_limit)
                if logs_result.get("success"):
                    activity_log = logs_result.get("logs", [])

        if not alert_result.get("success"):
            return {
                "source": "opsgenie",
                "available": False,
                "error": alert_result.get("error", "unknown error"),
                "alert": {},
                "activity_log": [],
            }

        return {
            "source": "opsgenie",
            "available": True,
            "alert_id": alert_id,
            "alert": alert,
            "activity_log": activity_log,
            "total_log_entries": len(activity_log),
        }


opsgenie_alert_detail = OpsGenieAlertDetailTool()


# ======== from tools/opsgenie_alerts_tool/ ========

"""OpsGenie alert listing and search investigation tool."""


from tools.base import BaseTool

_OPEN_STATUSES = {"open"}


class OpsGenieAlertsTool(BaseTool):
    """List and search OpsGenie alerts to surface active incidents and their triage state."""

    name = "opsgenie_alerts"
    source = "opsgenie"
    description = (
        "Search OpsGenie alerts to find active incidents, identify unacknowledged P1/P2 alerts, "
        "and correlate alert context with errors from Datadog, Sentry, or other sources."
    )
    use_cases = [
        "Listing open OpsGenie alerts for an ongoing incident",
        "Finding unacknowledged high-priority alerts",
        "Correlating an OpsGenie alert with errors in Datadog or Sentry",
        "Checking recent alert history for a service or tag",
    ]
    requires = ["api_key"]
    injected_params = ["api_key"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "OpsGenie API key (GenieKey)"},
            "region": {
                "type": "string",
                "default": "us",
                "description": "OpsGenie region: us or eu",
            },
            "query": {
                "type": "string",
                "default": "",
                "description": "OpsGenie alert search query (e.g. status=open, tag=env:prod)",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum number of alerts to return",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "alerts": "List of alerts with status, priority, tags, and timestamps",
        "open_alerts": "Subset of alerts in open state",
        "total": "Total number of alerts returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("opsgenie", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        og = sources["opsgenie"]
        return {
            "api_key": og.get("api_key", ""),
            "region": og.get("region", "us"),
            "query": og.get("query", ""),
            "limit": 20,
        }

    def run(
        self,
        api_key: str,
        region: str = "us",
        query: str = "",
        limit: int = 20,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_opsgenie_client(api_key, region)
        if client is None:
            return {
                "source": "opsgenie",
                "available": False,
                "error": "OpsGenie integration is not configured.",
                "alerts": [],
                "open_alerts": [],
                "total": 0,
            }

        with client:
            result = client.list_alerts(query=query, limit=limit)

        if not result.get("success"):
            return {
                "source": "opsgenie",
                "available": False,
                "error": result.get("error", "unknown error"),
                "alerts": [],
                "open_alerts": [],
                "total": 0,
            }

        alerts = result.get("alerts", [])
        open_alerts = [a for a in alerts if a.get("status", "").lower() in _OPEN_STATUSES]
        return {
            "source": "opsgenie",
            "available": True,
            "alerts": alerts,
            "open_alerts": open_alerts,
            "total": len(alerts),
            "query": query,
        }


opsgenie_alerts = OpsGenieAlertsTool()
