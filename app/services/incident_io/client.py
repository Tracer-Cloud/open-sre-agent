"""Incident.io REST API client.

Wraps the incident.io API endpoints used for alert investigation and triage.
Credentials come from the user's incident.io integration stored locally or via env vars.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.integrations.config_models import IncidentIoIntegrationConfig
from app.integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 3
# NOTE: httpx.HTTPTransport(retries=N) only retries on connection-level errors
# (ConnectError / ConnectTimeout). It does NOT retry on HTTP 429 or 5xx responses.
# To retry on rate-limits/server errors, we use a custom backoff loop in _request().

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

    _lock = threading.Lock()

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
            # Use _request to benefit from 429/5xx retry logic during verification
            resp = self._request("GET", "/v2/incidents", params={"page_size": 1})
            resp.raise_for_status()
        except Exception as e:
            err_text = self._redact(str(e))
            return ProbeResult.failed(f"Connection failed: {err_text}", region=self.config.region)

        return ProbeResult.passed(
            f"Connected to incident.io ({self.config.region.upper()} region); API key accepted.",
            region=self.config.region,
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Perform an HTTP request with exponential backoff for 429 and 5xx errors."""
        client = self._get_client()
        last_exception: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = client.request(method, url, **kwargs)
                # Retry on rate limit (429) or transient server errors (5xx)
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                last_exception = e
                # Only retry if it's a transient error
                if e.response.status_code not in (429, 500, 502, 503, 504):
                    raise e

                if attempt < _MAX_RETRIES:
                    # Only retry if the method is idempotent
                    if method.upper() not in ("GET", "HEAD", "OPTIONS"):
                        raise e

                    sleep_time = (2**attempt) + (random.random() * 0.1)
                    logger.warning(
                        "[incident_io] Request %s %s failed (%s); retrying in %.2fs (attempt %s/%s)",
                        method,
                        url,
                        e.response.status_code,
                        sleep_time,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    time.sleep(sleep_time)
            except httpx.RequestError as e:
                last_exception = e
                # Connection errors are partly handled by HTTPTransport, but we add
                # another layer here for robustness across different error types.
                if attempt < _MAX_RETRIES:
                    # Only retry if the method is idempotent
                    if method.upper() not in ("GET", "HEAD", "OPTIONS"):
                        raise e

                    sleep_time = (2**attempt) + (random.random() * 0.1)
                    time.sleep(sleep_time)
                else:
                    raise e

        if last_exception:
            raise last_exception
        raise RuntimeError("Request failed after max retries without an exception")

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
            resp = self._request("GET", "/v2/incidents", params=params)
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
            err_text = self._redact(str(e))
            logger.warning("[incident_io] List incidents error: %s", err_text)
            return {"success": False, "error": err_text}

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch full details for a specific incident.io incident."""
        try:
            resp = self._request("GET", f"/v2/incidents/{incident_id}")
            resp.raise_for_status()
            data = resp.json().get("incident", {})

            incident = {
                "id": data.get("id", ""),
                "name": data.get("name", ""),
                "reference": data.get("reference", ""),
                "status": data.get("status", ""),
                "severity": data.get("severity", {}).get("name", ""),
                "summary": data.get("summary", ""),
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
            err_text = self._redact(str(e))
            logger.warning("[incident_io] Get incident error: %s", err_text)
            return {"success": False, "error": err_text}

    def add_timeline_event(
        self, incident_id: str, title: str, description: str = ""
    ) -> dict[str, Any]:
        """Add findings to an incident timeline.

        Tries the dedicated timeline API first (atomic).
        Falls back to summary-append if timeline API is unavailable or returns 404.
        """
        payload = {
            "incident_id": incident_id,
            "content": f"**OpenSRE Finding: {title}**\n\n{description}",
        }
        try:
            resp = self._request("POST", "/v2/incident_timeline_events", json=payload)
            resp.raise_for_status()
            return {"success": True}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # If the timeline endpoint is non-existent, fallback to summary-append
                return self._add_timeline_event_via_summary(incident_id, title, description)

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
            err_text = self._redact(str(e))
            logger.warning("[incident_io] Add timeline event error: %s", err_text)
            return {"success": False, "error": err_text}

    def _add_timeline_event_via_summary(
        self, incident_id: str, title: str, description: str = ""
    ) -> dict[str, Any]:
        """Fallback method to append findings to the incident summary.

        Uses a process-level lock to mitigate (but not fully eliminate) races
        during the read-modify-write cycle.
        """
        with self._lock:
            try:
                # Fetch existing incident to get the current summary
                get_res = self.get_incident(incident_id)
                if not get_res["success"]:
                    return get_res

                incident_data = get_res["incident"]
                current_summary = incident_data.get("summary") or ""

                # Format the new finding as a markdown append
                timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
                new_finding = f"\n\n---\n**OpenSRE Finding: {title}** ({timestamp})\n{description}"
                updated_summary = (current_summary + new_finding).strip()

                payload = {
                    "incident": {"summary": updated_summary},
                    "notify_incident_channel": False,
                }
                resp = self._request(
                    "POST",
                    f"/v2/incidents/{incident_id}/actions/edit",
                    json=payload,
                )
                resp.raise_for_status()
                return {"success": True}
            except httpx.HTTPStatusError as e:
                err_text = self._redact(e.response.text[:200])
                logger.warning(
                    "[incident_io] Summary-append fallback HTTP failure status=%s id=%r error=%r",
                    e.response.status_code,
                    incident_id,
                    err_text,
                )
                return {
                    "success": False,
                    "error": f"HTTP {e.response.status_code}: {err_text}",
                }
            except Exception as e:
                err_text = self._redact(str(e))
                logger.warning("[incident_io] Summary-append fallback error: %s", err_text)
                return {"success": False, "error": err_text}


def make_incident_io_client(
    api_key: str | None, region: str | None = "us", base_url: str | None = None
) -> IncidentIoClient | None:
    """Create an IncidentIoClient if a valid API key is provided."""
    token = (api_key or "").strip()
    if not token:
        return None
    try:
        return IncidentIoClient(
            IncidentIoIntegrationConfig(
                api_key=token, region=region or "us", base_url=base_url or ""
            )
        )
    except Exception as e:
        logger.warning("[incident_io] Failed to build IncidentIoClient from config: %s", e)
        return None
