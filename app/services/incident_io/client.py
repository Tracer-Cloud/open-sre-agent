"""Incident.io REST API client.

Wraps the incident.io API endpoints used for alert investigation and triage.
Credentials come from the user's incident.io integration stored locally or via env vars.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.integrations.config_models import IncidentIoIntegrationConfig
from app.integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
IncidentIoConfig = IncidentIoIntegrationConfig


class IncidentIoClient:
    """Synchronous client for querying the incident.io API."""

    def __init__(self, config: IncidentIoConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key)

    def probe_access(self) -> ProbeResult:
        """Validate incident.io credentials with a minimal incidents list call."""
        if not self.is_configured:
            return ProbeResult.missing("Missing API key.")

        with self:
            result = self.list_incidents(status="")
            # We don't mind if there are no open incidents, just need a successful HTTP response

        if not result.get("success"):
            return ProbeResult.failed(
                f"Incident list check failed: {result.get('error', 'unknown error')}"
            )

        return ProbeResult.passed("Connected to incident.io; API key accepted.")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> IncidentIoClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_incidents(
        self,
        status: str = "open",
        page_size: int | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        """List incident.io incidents, optionally filtered by status.
        Status can be e.g. open, closed, or omitted.
        """
        params: dict[str, Any] = {}
        if status:
            params["status[in]"] = status
        if page_size is not None:
            params["page_size"] = page_size
        if after:
            params["after"] = after

        try:
            resp = self._get_client().get("/v2/incidents", params=params)
            resp.raise_for_status()
            data = resp.json()

            incidents = []
            for a in data.get("incidents", []):
                incidents.append(
                    {
                        "id": a.get("id", ""),
                        "name": a.get("name", ""),
                        "reference": a.get("reference", ""),
                        "status": a.get("status", ""),
                        "severity": a.get("severity", {}).get("name", ""),
                        "created_at": a.get("created_at", ""),
                        "updated_at": a.get("updated_at", ""),
                        "incident_role_assignments": [
                            {
                                "role": r.get("role", {}).get("name", ""),
                                "assignee": r.get("assignee", {}).get("name", ""),
                            }
                            for r in a.get("incident_role_assignments", [])
                        ],
                    }
                )

            result = {"success": True, "incidents": incidents, "total": len(incidents)}
            if "pagination_meta" in data:
                result["pagination_meta"] = data["pagination_meta"]
            return result
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[incident_io] List incidents HTTP failure status=%s",
                e.response.status_code,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[incident_io] List incidents error: %s", e)
            return {"success": False, "error": str(e)}

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch full details for a specific incident.io incident."""
        try:
            resp = self._get_client().get(f"/v2/incidents/{incident_id}")
            resp.raise_for_status()
            data = resp.json().get("incident", {})

            incident = {
                "id": data.get("id", ""),
                "name": data.get("name", ""),
                "reference": data.get("reference", ""),
                "status": data.get("status", ""),
                "severity": data.get("severity", {}).get("name", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "custom_fields": data.get("custom_field_entries", []),
            }

            return {"success": True, "incident": incident}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[incident_io] Get incident HTTP failure status=%s id=%r",
                e.response.status_code,
                incident_id,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[incident_io] Get incident error: %s", e)
            return {"success": False, "error": str(e)}

    def add_timeline_event(
        self, incident_id: str, title: str, description: str = ""
    ) -> dict[str, Any]:
        """Add a custom event to an incident's timeline (findings write-back)."""

        try:
            payload = {
                "incident_id": incident_id,
                "event_type": "custom",
                "title": title,
                "description": description,
                "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            }
            resp = self._get_client().post(
                "/v2/incident_timeline_events",
                json=payload,
            )
            resp.raise_for_status()
            return {"success": True}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[incident_io] Add timeline event HTTP failure status=%s id=%r",
                e.response.status_code,
                incident_id,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[incident_io] Add timeline event error: %s", e)
            return {"success": False, "error": str(e)}


def make_incident_io_client(api_key: str | None) -> IncidentIoClient | None:
    """Create an IncidentIoClient if a valid API key is provided."""
    token = (api_key or "").strip()
    if not token:
        return None
    try:
        return IncidentIoClient(IncidentIoConfig(api_key=token))
    except Exception:
        return None
