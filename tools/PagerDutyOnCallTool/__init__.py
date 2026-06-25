"""PagerDuty on-call schedule investigation tool."""

from __future__ import annotations

from typing import Any

from services.pagerduty import make_pagerduty_client
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
