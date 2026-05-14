"""Small ServiceNow Table API client for investigation context."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

import httpx

from app.integrations.config_models import ServiceNowIntegrationConfig
from app.integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

ServiceNowConfig = ServiceNowIntegrationConfig

_DEFAULT_TIMEOUT = 30
_MAX_LIMIT = 100
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|authorization\s*[:=]\s*\S+"
    r"|servicenow[_-]?(api[_-]?)?token\s*[:=]\s*\S+"
    r"|password\s*[:=]\s*\S+)"
)

_INCIDENT_FIELDS = (
    "sys_id,number,short_description,description,state,priority,urgency,"
    "assignment_group,cmdb_ci,business_service,opened_at,sys_updated_on"
)
_CHANGE_FIELDS = (
    "sys_id,number,short_description,type,state,risk,impact,assignment_group,"
    "cmdb_ci,business_service,start_date,end_date,sys_updated_on"
)
_SERVICE_FIELDS = "sys_id,name,owned_by,operational_status,busines_criticality"


def _limit(value: int | None, default: int = 10) -> int:
    return default if value is None else max(1, min(int(value), _MAX_LIMIT))


def _record(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            str(value.get("display_value") or value.get("value") or "").strip()
            if isinstance(value, dict)
            else value
        )
        for key, value in data.items()
    }


class ServiceNowClient:
    """Synchronous client for the ServiceNow Table API."""

    def __init__(self, config: ServiceNowConfig) -> None:
        self.config = config
        use_basic_auth = bool(config.username and config.password)
        auth = (config.username, config.password) if use_basic_auth else None
        headers = dict(config.headers)
        if use_basic_auth:
            headers.pop("Authorization", None)
        self._client = httpx.Client(
            base_url=config.base_url,
            headers=headers,
            auth=auth,
            timeout=_DEFAULT_TIMEOUT,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_token or (self.config.username and self.config.password))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ServiceNowClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def probe_access(self) -> ProbeResult:
        if not self.is_configured:
            return ProbeResult.missing("Missing ServiceNow credentials.")
        try:
            self._rows("incident", fields="sys_id,number", limit=1)
        except Exception as exc:
            return ProbeResult.failed(
                f"Connection failed: {self._redact(exc)}",
                base_url=self.config.base_url,
            )
        return ProbeResult.passed(
            "Connected to ServiceNow; credentials accepted.",
            base_url=self.config.base_url,
        )

    def list_incidents(
        self, *, query: str = "active=true", limit: int | None = 10
    ) -> dict[str, Any]:
        return self._list(
            "List incidents",
            "incident",
            "incidents",
            query=query or "active=true",
            fields=_INCIDENT_FIELDS,
            limit=limit,
        )

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch an incident by sys_id or number."""

        def load() -> dict[str, Any]:
            if incident_id.upper().startswith("INC"):
                rows = self._rows("incident", query=f"number={incident_id}", limit=1)
                return _record(rows[0]) if rows else {}
            return _record(self._row("incident", incident_id))

        return self._capture("Get incident", lambda: {"success": True, "incident": load()})

    def list_recent_changes(
        self,
        *,
        query: str = "active=true^ORDERBYDESCsys_updated_on",
        limit: int | None = 10,
    ) -> dict[str, Any]:
        return self._list(
            "List changes",
            "change_request",
            "changes",
            query=query or "active=true^ORDERBYDESCsys_updated_on",
            fields=_CHANGE_FIELDS,
            limit=limit,
        )

    def list_services(self, *, query: str = "", limit: int | None = 10) -> dict[str, Any]:
        return self._list(
            "List services",
            "cmdb_ci_service",
            "services",
            query=query,
            fields=_SERVICE_FIELDS,
            limit=limit,
        )

    def get_context(
        self,
        incident_id: str,
        *,
        change_query: str = "active=true^ORDERBYDESCsys_updated_on",
        service_query: str = "",
        limit: int | None = 10,
    ) -> dict[str, Any]:
        incident = self.get_incident(incident_id)
        if not incident.get("success"):
            return incident
        changes = self.list_recent_changes(query=change_query, limit=limit)
        services = self.list_services(query=service_query, limit=limit)
        return {
            "success": changes.get("success", False) and services.get("success", False),
            "incident": incident.get("incident", {}),
            "changes": changes.get("changes", []),
            "services": services.get("services", []),
            "errors": [
                str(result.get("error"))
                for result in (changes, services)
                if not result.get("success") and result.get("error")
            ],
        }

    def append_work_note(self, incident_id: str, note: str) -> dict[str, Any]:
        if not note.strip():
            return {"success": False, "error": "note is required for append_work_note."}

        def patch() -> dict[str, Any]:
            target = incident_id
            if incident_id.upper().startswith("INC"):
                target = str(self.get_incident(incident_id).get("incident", {}).get("sys_id") or "")
            if not target:
                return {"success": False, "error": "incident was not found."}
            response = self._client.patch(
                f"/api/now/table/incident/{target}",
                json={"work_notes": note},
                params={"sysparm_display_value": "true", "sysparm_exclude_reference_link": "true"},
            )
            response.raise_for_status()
            return {"success": True, "incident": _record(response.json().get("result", {}))}

        return self._capture("Append work note", patch)

    def _list(
        self,
        action: str,
        table: str,
        key: str,
        *,
        query: str,
        fields: str,
        limit: int | None,
    ) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            records = [
                _record(row) for row in self._rows(table, query=query, fields=fields, limit=limit)
            ]
            return {"success": True, key: records, "total": len(records)}

        return self._capture(
            action,
            load,
        )

    def _rows(
        self,
        table: str,
        *,
        query: str = "",
        fields: str = "",
        limit: int | None = 10,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "sysparm_limit": _limit(limit),
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
        }
        if query:
            params["sysparm_query"] = query
        if fields:
            params["sysparm_fields"] = fields
        response = self._client.get(f"/api/now/table/{table}", params=params)
        response.raise_for_status()
        result = response.json().get("result", [])
        return result if isinstance(result, list) else []

    def _row(self, table: str, sys_id: str) -> dict[str, Any]:
        response = self._client.get(
            f"/api/now/table/{table}/{sys_id}",
            params={"sysparm_display_value": "true", "sysparm_exclude_reference_link": "true"},
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        return result if isinstance(result, dict) else {}

    def _capture(self, action: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fn()
        except httpx.HTTPStatusError as exc:
            detail = self._redact(exc.response.text[:300])
            logger.warning(
                "[servicenow] %s HTTP failure status=%s error=%r",
                action,
                exc.response.status_code,
                detail,
            )
            return {"success": False, "error": f"HTTP {exc.response.status_code}: {detail}"}
        except Exception as exc:
            detail = self._redact(exc)
            logger.warning("[servicenow] %s error: %s", action, detail)
            return {"success": False, "error": detail}

    def _redact(self, value: object) -> str:
        text = str(value)
        for secret in (self.config.api_token, self.config.password):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return _SECRET_RE.sub("[REDACTED]", text)


def make_servicenow_client(
    instance_url: str | None,
    *,
    username: str | None = "",
    password: str | None = "",
    api_token: str | None = "",
) -> ServiceNowClient | None:
    """Create a ServiceNow client if usable credentials are provided."""
    try:
        return ServiceNowClient(
            ServiceNowConfig(
                instance_url=instance_url or "",
                username=username or "",
                password=password or "",
                api_token=api_token or "",
            )
        )
    except Exception as exc:
        logger.warning("[servicenow] Failed to build client config: %s", exc)
        return None
