"""Incident.io REST API client.

Wraps the incident.io API endpoints used for alert investigation and triage.
Credentials come from the user's incident.io integration stored locally or via env vars.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.integrations.config_models import IncidentIoIntegrationConfig
from app.integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 3

_GENERIC_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|authorization\s*[:=]\s*\S+"
    r"|xox[baprs]-[A-Za-z0-9-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)


class IncidentIoClient:
    """Synchronous client for querying the incident.io API."""

    def __init__(self, config: IncidentIoIntegrationConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            # Add retry transport for transient 5xx and 429s
            transport = httpx.HTTPTransport(retries=_MAX_RETRIES)
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT,
                transport=transport,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key)

    def probe_access(self) -> ProbeResult:
        """Validate incident.io credentials with a minimal incidents list check."""
        if not self.is_configured:
            return ProbeResult.missing("Missing API key.")

        try:
            # Use a short-lived client for the probe to avoid leaking persistent connections
            transport = httpx.HTTPTransport(retries=_MAX_RETRIES)
            with httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT,
                transport=transport,
            ) as client:
                resp = client.get("/v2/incidents", params={"page_size": 1})
                resp.raise_for_status()
        except Exception as e:
            return ProbeResult.failed(f"Connection failed: {e}", region=self.config.region)

        return ProbeResult.passed(
            f"Connected to incident.io ({self.config.region.upper()} region); API key accepted.",
            region=self.config.region,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _redact(self, value: object) -> str:
        """Redact the API key and generic secrets from surfaced text."""
        text = str(value)
        if self.config.api_key:
            text = text.replace(self.config.api_key, "[REDACTED]")
        return _GENERIC_SECRET_VALUE_RE.sub("[REDACTED]", text)

    def __enter__(self) -> IncidentIoClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_incidents(
        self,
        status: str = "live",
        page_size: int | None = None,
        after: str | None = None,
    ) -> dict[str, Any]:
        """List incident.io incidents, optionally filtered by status."""
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
            # Redact response text to avoid leaking tokens in logs
            err_text = self._redact(e.response.text[:200])
            logger.warning(
                "[incident_io] List incidents HTTP failure status=%s error=%r",
                e.response.status_code,
                err_text,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {err_text}",
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
            err_text = self._redact(e.response.text[:200])
            logger.warning(
                "[incident_io] Get incident HTTP failure status=%s id=%r error=%r",
                e.response.status_code,
                incident_id,
                err_text,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {err_text}",
            }
        except Exception as e:
            logger.warning("[incident_io] Get incident error: %s", e)
            return {"success": False, "error": str(e)}

    def add_timeline_event(
        self, incident_id: str, title: str, description: str = ""
    ) -> dict[str, Any]:
        """Add a custom event to an incident's timeline (findings write-back)."""

        try:
            # Incident.io V2 API uses 'content' for the timeline event body.
            # We combine title and description into a single markdown block.
            content = f"### {title}"
            if description:
                content += f"\n\n{description}"

            payload = {
                "incident_id": incident_id,
                "content": content,
                "occurred_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            }
            resp = self._get_client().post(
                "/v2/incident_timeline_events",
                json=payload,
            )
            resp.raise_for_status()
            return {"success": True}
        except httpx.HTTPStatusError as e:
            err_text = self._redact(e.response.text[:200])
            logger.warning(
                "[incident_io] Add timeline event HTTP failure status=%s id=%r error=%r",
                e.response.status_code,
                incident_id,
                err_text,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {err_text}",
            }
        except Exception as e:
            logger.warning("[incident_io] Add timeline event error: %s", e)
            return {"success": False, "error": str(e)}


def make_incident_io_client(
    api_key: str | None, region: str | None = "us"
) -> IncidentIoClient | None:
    """Create an IncidentIoClient if a valid API key is provided."""
    token = (api_key or "").strip()
    if not token:
        return None
    try:
        return IncidentIoClient(IncidentIoIntegrationConfig(api_key=token, region=region or "us"))
    except Exception as e:
        logger.warning("[incident_io] Failed to build IncidentIoClient from config: %s", e)
        return None
