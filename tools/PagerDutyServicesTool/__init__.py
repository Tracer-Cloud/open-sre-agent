"""PagerDuty services and escalation policies investigation tool."""

from __future__ import annotations

from typing import Any

from services.pagerduty import make_pagerduty_client
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
