# ======== from tools/opsgenie_alert_detail_tool/ ========

"""OpsGenie alert detail and activity log investigation tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.opsgenie.client import make_opsgenie_client

#: OpsGenie's REST API caps every list endpoint's page size at 100
#: (``min(limit, 100)`` in ``integrations/opsgenie/client.py``), and the
#: client discards the API's own pagination metadata -- a returned page
#: cannot be distinguished from a true total except by comparing it against
#: the effective page size that was requested.
_OPSGENIE_MAX_PAGE_SIZE = 100

#: Alert messages are free-form, human-entered OpsGenie text -- unbounded
#: and can contain newlines or carriage returns. Cap the length used in a
#: report summary so one long or multi-line value can't produce a malformed
#: or oversized report line.
_MESSAGE_SUMMARY_TRUNCATE_LEN = 120


def _opsgenie_summary_text(value: str) -> str:
    """Collapse and cap free-form OpsGenie text before it goes into a summary."""
    collapsed = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return truncate(collapsed, _MESSAGE_SUMMARY_TRUNCATE_LEN)


def _opsgenie_page_is_truncated(returned_count: int, requested_limit: int) -> bool:
    effective_limit = min(max(requested_limit, 1), _OPSGENIE_MAX_PAGE_SIZE)
    return returned_count >= effective_limit


def _opsgenie_count_label(count: int, requested_limit: int) -> str:
    """Format a list count, appending "+" when the page may be truncated."""
    return f"{count}+" if _opsgenie_page_is_truncated(count, requested_limit) else str(count)


def _map_opsgenie_alert_detail(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the alert's message, status/priority, and activity log entry count."""
    if not output.get("available"):
        return
    alert = output.get("alert") or {}
    if not alert:
        return
    message = _opsgenie_summary_text(str(alert.get("message", "unknown")))
    parts = [f"'{message}'", alert.get("status", "unknown")]
    if alert.get("priority"):
        parts.append(str(alert["priority"]))
    total_log_entries = output.get("total_log_entries", 0)
    if total_log_entries:
        label = _opsgenie_count_label(total_log_entries, tool_input.get("log_limit", 20))
        parts.append(f"{label} log entries")
    record_evidence_entry(
        evidence,
        source="opsgenie_alert_detail",
        label="OpsGenie Alert Detail",
        summary=", ".join(parts),
    )


class OpsGenieAlertDetailTool(BaseTool):
    """Fetch full details and activity log for a specific OpsGenie alert."""

    name = "opsgenie_alert_detail"
    source = "opsgenie"
    evidence_mapper = _map_opsgenie_alert_detail
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
            return tool_unavailable(
                "opsgenie",
                "alert_id is required. Run opsgenie_alerts first to find an alert ID.",
                alert={},
                activity_log=[],
            )

        client = make_opsgenie_client(api_key, region)
        if client is None:
            return tool_unavailable(
                "opsgenie", "OpsGenie integration is not configured.", alert={}, activity_log=[]
            )

        with client:
            alert_result = client.get_alert(alert_id)
            alert = alert_result.get("alert", {}) if alert_result.get("success") else {}

            activity_log: list[dict[str, Any]] = []
            if alert_result.get("success") and include_activity_log:
                logs_result = client.get_alert_logs(alert_id, limit=log_limit)
                if logs_result.get("success"):
                    activity_log = logs_result.get("logs", [])

        if not alert_result.get("success"):
            return tool_unavailable(
                "opsgenie", alert_result.get("error", "unknown error"), alert={}, activity_log=[]
            )

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


from core.tool import BaseTool

_OPEN_STATUSES = {"open"}


def _map_opsgenie_alerts(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the alert count and how many are open.

    The open-alert sub-count inherits the same page-saturation flag as the
    total: on a truncated page it is a count of open alerts *within the
    returned page*, not necessarily every open alert.
    """
    if not output.get("available"):
        return
    alerts = output.get("alerts") or []
    if not alerts:
        return
    total = output.get("total", len(alerts))
    requested_limit = tool_input.get("limit", 20)
    truncated = _opsgenie_page_is_truncated(total, requested_limit)
    total_label = f"{total}+" if truncated else str(total)
    summary = f"{total_label} alert(s)"
    # alerts is non-empty here (checked above), so a zero open_count is a
    # genuine "0 of N open" finding worth citing, not noise to suppress.
    open_count = len(output.get("open_alerts") or [])
    # A truncated page's open-count is only a floor even when it's zero --
    # zero open alerts *in the returned page* does not mean zero overall.
    open_label = f"{open_count}+" if truncated else str(open_count)
    summary += f", {open_label} open"
    record_evidence_entry(
        evidence,
        source="opsgenie_alerts",
        label="OpsGenie Alerts",
        summary=summary,
    )


class OpsGenieAlertsTool(BaseTool):
    """List and search OpsGenie alerts to surface active incidents and their triage state."""

    name = "opsgenie_alerts"
    source = "opsgenie"
    evidence_mapper = _map_opsgenie_alerts
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
            return tool_unavailable(
                "opsgenie",
                "OpsGenie integration is not configured.",
                alerts=[],
                open_alerts=[],
                total=0,
            )

        with client:
            result = client.list_alerts(query=query, limit=limit)

        if not result.get("success"):
            return tool_unavailable(
                "opsgenie", result.get("error", "unknown error"), alerts=[], open_alerts=[], total=0
            )

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
