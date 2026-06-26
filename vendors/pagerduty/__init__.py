# ======== from tools/pagerduty_incident_detail_tool/ ========

"""PagerDuty incident detail and timeline investigation tool."""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool
from vendors.pagerduty.client import make_pagerduty_client


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


# ======== from tools/pagerduty_incidents_tool/ ========

"""PagerDuty incident listing and search investigation tool."""


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


# ======== from tools/pagerduty_on_call_tool/ ========

"""PagerDuty on-call schedule investigation tool."""


from tools.base import BaseTool


class PagerDutyOnCallTool(BaseTool):
    """Fetch current on-call responders from PagerDuty escalation policies."""

    name = "pagerduty_oncall"
    source = "pagerduty"
    description = (
        "Fetch current on-call responders for PagerDuty escalation policies to identify "
        "who is responsible for responding to an active incident or service."
    )
    use_cases = [
        "Finding who is currently on-call for a specific escalation policy",
        "Identifying responders during an active incident investigation",
        "Checking on-call coverage across escalation levels",
        "Correlating responder availability with incident response times",
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
            "escalation_policy_ids": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Filter by escalation policy IDs (returns all if empty)",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Maximum number of on-call entries to return",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "oncalls": "List of on-call entries with user, escalation policy, level, and schedule",
        "total": "Total number of on-call entries returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("pagerduty", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        pd = sources["pagerduty"]
        return {
            "api_key": pd.get("api_key", ""),
            "base_url": pd.get("base_url", ""),
            "escalation_policy_ids": [],
            "limit": 25,
        }

    def run(
        self,
        api_key: str,
        base_url: str = "",
        escalation_policy_ids: list[str] | None = None,
        limit: int = 25,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_pagerduty_client(api_key, base_url or None)
        if client is None:
            return {
                "source": "pagerduty",
                "available": False,
                "error": "PagerDuty integration is not configured.",
                "oncalls": [],
                "total": 0,
            }

        with client:
            result = client.get_oncalls(
                escalation_policy_ids=escalation_policy_ids or None,
                limit=limit,
            )

        if not result.get("success"):
            return {
                "source": "pagerduty",
                "available": False,
                "error": result.get("error", "unknown error"),
                "oncalls": [],
                "total": 0,
            }

        oncalls = result.get("oncalls", [])
        return {
            "source": "pagerduty",
            "available": True,
            "oncalls": oncalls,
            "total": len(oncalls),
        }


pagerduty_oncall = PagerDutyOnCallTool()


# ======== from tools/pagerduty_services_tool/ ========

"""PagerDuty services and escalation policies investigation tool."""


from tools.base import BaseTool


class PagerDutyServicesTool(BaseTool):
    """Fetch PagerDuty services, escalation policies, and alert routing configuration."""

    name = "pagerduty_services"
    source = "pagerduty"
    description = (
        "Fetch PagerDuty services with their escalation policies, integrations, and alert "
        "routing rules to understand how alerts flow through the incident management system."
    )
    use_cases = [
        "Listing services to understand alert routing topology",
        "Finding which escalation policy handles a specific service",
        "Checking service integrations (monitoring tools routing alerts to PagerDuty)",
        "Getting service detail including urgency rules and team ownership",
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
            "service_id": {
                "type": "string",
                "default": "",
                "description": "Specific service ID to fetch detail for (lists all if empty)",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Maximum number of services to return (when listing)",
            },
        },
        "required": ["api_key"],
    }
    outputs = {
        "services": "List of services with escalation policies, integrations, and teams",
        "service": "Full service detail (when service_id is provided)",
        "total": "Total number of services returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("pagerduty", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        pd = sources["pagerduty"]
        return {
            "api_key": pd.get("api_key", ""),
            "base_url": pd.get("base_url", ""),
            "service_id": "",
            "limit": 25,
        }

    def run(
        self,
        api_key: str,
        base_url: str = "",
        service_id: str = "",
        limit: int = 25,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_pagerduty_client(api_key, base_url or None)
        if client is None:
            return {
                "source": "pagerduty",
                "available": False,
                "error": "PagerDuty integration is not configured.",
                "services": [],
                "service": {},
                "total": 0,
            }

        with client:
            if service_id:
                result = client.get_service(service_id)
                if not result.get("success"):
                    return {
                        "source": "pagerduty",
                        "available": False,
                        "error": result.get("error", "unknown error"),
                        "services": [],
                        "service": {},
                        "total": 0,
                    }
                service = result.get("service", {})
                return {
                    "source": "pagerduty",
                    "available": True,
                    "service_id": service_id,
                    "services": [],
                    "service": service,
                    "total": 1,
                }

            result = client.list_services(limit=limit)

        if not result.get("success"):
            return {
                "source": "pagerduty",
                "available": False,
                "error": result.get("error", "unknown error"),
                "services": [],
                "service": {},
                "total": 0,
            }

        services = result.get("services", [])
        return {
            "source": "pagerduty",
            "available": True,
            "services": services,
            "service": {},
            "total": len(services),
        }


pagerduty_services = PagerDutyServicesTool()
