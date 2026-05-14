"""ServiceNow incident, change, service, and work-note tool."""

from __future__ import annotations

from typing import Any

from app.services.servicenow import make_servicenow_client
from app.tools.base import BaseTool


class ServiceNowRecordsTool(BaseTool):
    """Read ServiceNow operational context and optionally append incident work notes."""

    name = "servicenow_records"
    source = "servicenow"
    description = (
        "Read ServiceNow incidents, related business services, and recent changes for RCA "
        "context. Can append OpenSRE findings to incident work notes when explicitly requested."
    )
    use_cases = [
        "Reading ServiceNow incident context linked from an alert",
        "Finding active incidents and recent change records during RCA",
        "Looking up ServiceNow business services from CMDB service records",
        "Posting investigation findings as ServiceNow incident work notes when requested",
    ]
    requires = ["instance_url", "api_token or username/password"]
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_incidents",
                    "get_incident",
                    "changes",
                    "services",
                    "context",
                    "append_work_note",
                ],
                "default": "context",
                "description": "ServiceNow action to perform.",
            },
            "incident_id": {
                "type": "string",
                "description": "ServiceNow incident sys_id or number, e.g. INC0010001.",
            },
            "incident_query": {
                "type": "string",
                "default": "active=true",
                "description": "ServiceNow encoded query for incident list.",
            },
            "change_query": {
                "type": "string",
                "default": "active=true^ORDERBYDESCsys_updated_on",
                "description": "ServiceNow encoded query for change_request records.",
            },
            "service_query": {
                "type": "string",
                "description": "ServiceNow encoded query for cmdb_ci_service records.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Maximum records to return.",
            },
            "note": {
                "type": "string",
                "description": "Work note body for append_work_note.",
            },
        },
        "required": [],
    }
    outputs = {
        "incidents": "List of ServiceNow incident summaries",
        "incident": "ServiceNow incident detail for a single incident",
        "changes": "Recent ServiceNow change_request records",
        "services": "ServiceNow cmdb_ci_service records",
        "success": "Whether the action succeeded",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("servicenow", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        servicenow = sources.get("servicenow", {})
        incident_id = str(servicenow.get("incident_id", "")).strip()
        return {
            "instance_url": servicenow.get("instance_url", ""),
            "username": servicenow.get("username", ""),
            "password": servicenow.get("password", ""),
            "api_token": servicenow.get("api_token", ""),
            "action": "context" if incident_id else "list_incidents",
            "incident_id": incident_id,
            "incident_query": servicenow.get("incident_query", "active=true"),
            "change_query": servicenow.get("change_query", "active=true^ORDERBYDESCsys_updated_on"),
            "service_query": servicenow.get("service_query", ""),
            "limit": servicenow.get("limit", 10),
        }

    def run(
        self,
        instance_url: str,
        *,
        username: str = "",
        password: str = "",
        api_token: str = "",
        action: str = "context",
        incident_id: str = "",
        incident_query: str = "active=true",
        change_query: str = "active=true^ORDERBYDESCsys_updated_on",
        service_query: str = "",
        limit: int | None = 10,
        note: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_servicenow_client(
            instance_url,
            username=username,
            password=password,
            api_token=api_token,
        )
        if client is None:
            return {
                "source": "servicenow",
                "available": False,
                "success": False,
                "error": "ServiceNow integration is not configured.",
            }

        normalized_action = (action or "context").strip().lower()
        with client:
            if normalized_action == "list_incidents":
                result = client.list_incidents(query=incident_query, limit=limit)
            elif normalized_action == "get_incident":
                result = (
                    client.get_incident(incident_id)
                    if incident_id
                    else {"success": False, "error": "incident_id is required for get_incident."}
                )
            elif normalized_action == "changes":
                result = client.list_recent_changes(query=change_query, limit=limit)
            elif normalized_action == "services":
                result = client.list_services(query=service_query, limit=limit)
            elif normalized_action == "append_work_note":
                if not incident_id:
                    result = {
                        "success": False,
                        "error": "incident_id is required for append_work_note.",
                    }
                else:
                    result = client.append_work_note(incident_id, note)
            else:
                if not incident_id:
                    result = {"success": False, "error": "incident_id is required for context."}
                else:
                    result = client.get_context(
                        incident_id,
                        change_query=change_query,
                        service_query=service_query,
                        limit=limit,
                    )

        result.update(
            {
                "source": "servicenow",
                "available": bool(result.get("success")),
                "action": normalized_action,
            }
        )
        return result


servicenow_records = ServiceNowRecordsTool()
