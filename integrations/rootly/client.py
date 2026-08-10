"""Rootly REST API client for both incident context and on-call lookup.

Rootly speaks **JSON:API**: every request and response carries
``application/vnd.api+json``, and a resource arrives as
``{"data": {"id", "type", "attributes"}}`` rather than a flat object. A plain
``application/json`` POST is rejected outright, which is the single easiest
thing to get wrong here — the content type lives on the config's ``headers``
so no call site can forget it.

Responses are flattened to plain dicts before they leave this module: the
envelope is transport detail, and an LLM reading ``data.attributes.title``
through three layers of nesting wastes context for no gain.

This client now covers both Rootly products: incidents and on-call. The
on-call half degrades independently when the account lacks that entitlement.
"""

from __future__ import annotations

import logging
import re
import threading
from http import HTTPStatus
from typing import Any

import httpx

from integrations.config_models import RootlyIntegrationConfig
from integrations.path_safety import safe_path_segment
from integrations.probes import ProbeResult
from integrations.rootly.alerts import (
    ALERT_GET_UNENTITLED_STATUSES,
    ALERTS_PATH,
    ALERTS_UNENTITLED_STATUSES,
    alerts_entitlement_error,
    shape_alert,
)
from integrations.rootly.jsonapi import (
    attributes,
    clamp,
    data_list,
    meta_total,
    named,
    truncate,
)
from integrations.rootly.on_call import (
    ESCALATION_POLICIES_PATH,
    ON_CALL_PATH,
    ON_CALL_UNENTITLED_STATUSES,
    SCHEDULES_PATH,
    index_included_users,
    on_call_entitlement_error,
    shape_escalation_policy,
    shape_on_call,
    shape_schedule,
)
from platform.observability.errors.service import capture_service_error

logger = logging.getLogger(__name__)

RootlyConfig = RootlyIntegrationConfig

# Rootly allows 3000 reads/min per key, so the caps here are about prompt size,
# not rate limits: every incident returned is tokens the model has to read.
_MAX_PAGE_SIZE = 50
_DEFAULT_PAGE_SIZE = 20
_MAX_EVENT_PAGE_SIZE = 100
_DEFAULT_EVENT_PAGE_SIZE = 50
_MAX_ON_CALL_LIMIT = 50
_DEFAULT_ON_CALL_LIMIT = 20
_MAX_SUMMARY_CHARS = 2000
_ERROR_DETAIL_CHARS = 300

_INTERNAL_VISIBILITY = "internal"
_EXTERNAL_VISIBILITY = "external"
_VISIBILITIES = (_INTERNAL_VISIBILITY, _EXTERNAL_VISIBILITY)

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|authorization\s*[:=]\s*\S+"
    r"|rootly[_-]?(api[_-]?)?(token|key)\s*[:=]\s*\S+)"
)


def _shape_incident(record: Any, *, full: bool = False) -> dict[str, Any]:
    """Flatten one incident resource into the fields an RCA actually reads."""
    attrs = attributes(record)
    shaped: dict[str, Any] = {
        "id": str(record.get("id", "")) if isinstance(record, dict) else "",
        "sequential_id": attrs.get("sequential_id"),
        "slug": attrs.get("slug", ""),
        "title": attrs.get("title", ""),
        "status": attrs.get("status", ""),
        "severity": named(attrs.get("severity")),
        "url": attrs.get("url", ""),
        "started_at": attrs.get("started_at", ""),
        "created_at": attrs.get("created_at", ""),
        "resolved_at": attrs.get("resolved_at", ""),
    }
    if full:
        shaped.update(
            {
                "summary": truncate(str(attrs.get("summary") or ""), _MAX_SUMMARY_CHARS),
                "kind": attrs.get("kind", ""),
                "labels": attrs.get("labels", {}),
                "mitigated_at": attrs.get("mitigated_at", ""),
                "acknowledged_at": attrs.get("acknowledged_at", ""),
                "slack_channel_url": attrs.get("slack_channel_url", ""),
                "short_url": attrs.get("short_url", ""),
            }
        )
    return shaped


def _shape_event(record: Any) -> dict[str, Any]:
    attrs = attributes(record)
    return {
        "id": str(record.get("id", "")) if isinstance(record, dict) else "",
        "event": attrs.get("event", ""),
        "visibility": attrs.get("visibility", ""),
        "kind": attrs.get("kind", ""),
        "occurred_at": attrs.get("occurred_at", ""),
        "created_at": attrs.get("created_at", ""),
    }


def normalize_visibility(value: str | None) -> str:
    """Anything that is not an explicit ``external`` stays internal.

    Defaulting the other way would publish an agent-written note to a customer
    facing timeline on a typo.
    """
    candidate = (value or "").strip().lower()
    return candidate if candidate in _VISIBILITIES else _INTERNAL_VISIBILITY


class RootlyClient:
    """Synchronous client for the Rootly v1 API."""

    def __init__(self, config: RootlyConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None
        self._client_lock = threading.RLock()

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(
                    base_url=self.config.base_url,
                    headers=self.config.headers,
                    timeout=self.config.timeout_seconds,
                )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def __enter__(self) -> RootlyClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _redact(self, value: object) -> str:
        text = str(value)
        if self.config.api_token:
            text = text.replace(self.config.api_token, "[REDACTED]")
        return _SECRET_RE.sub("[REDACTED]", text)

    def _error(self, method: str, exc: Exception) -> dict[str, Any]:
        """Structured failure with the token scrubbed out of every path."""
        capture_service_error(exc, logger=logger, integration="rootly", method=method)
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == HTTPStatus.TOO_MANY_REQUESTS:
                return {
                    "success": False,
                    "error": "Rootly rate limit exceeded (HTTP 429); retry shortly.",
                }
            detail = self._redact(exc.response.text[:_ERROR_DETAIL_CHARS])
            return {"success": False, "error": f"HTTP {status}: {detail}"}
        return {"success": False, "error": self._redact(exc)}

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = self._get_client().get(path, params=params)
        response.raise_for_status()
        return response.json()

    def probe_access(self) -> ProbeResult:
        """Validate the token with the smallest possible incident listing."""
        if not self.is_configured:
            return ProbeResult.missing("Missing API token.")
        try:
            self._get("/v1/incidents", {"page[size]": 1})
        except Exception as exc:
            return ProbeResult.failed(
                f"Connection failed: {self._redact(exc)}",
                base_url=self.config.base_url,
            )
        return ProbeResult.passed(
            "Connected to Rootly; API token accepted.",
            base_url=self.config.base_url,
        )

    def list_incidents(
        self,
        *,
        status: str = "",
        severity: str = "",
        created_after: str = "",
        search: str = "",
        page_size: int | None = _DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List incidents newest-first, optionally filtered."""
        size = clamp(page_size, _DEFAULT_PAGE_SIZE, _MAX_PAGE_SIZE)
        params: dict[str, Any] = {"page[size]": size, "sort": "-created_at"}
        if status:
            params["filter[status]"] = status
        if severity:
            params["filter[severity]"] = severity
        if created_after:
            params["filter[created_at][gte]"] = created_after
        if search:
            params["filter[search]"] = search

        try:
            payload = self._get("/v1/incidents", params)
        except Exception as exc:
            return self._error("list_incidents", exc)

        incidents = [_shape_incident(item) for item in data_list(payload)]
        total = meta_total(payload)
        return {
            "success": True,
            "incidents": incidents,
            "returned": len(incidents),
            "total": total if total is not None else len(incidents),
            "truncated": bool(total is not None and total > len(incidents)),
        }

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch one incident with its related services and environments."""
        safe_id = safe_path_segment(incident_id)
        if safe_id is None:
            return {"success": False, "error": "incident_id is not a valid Rootly incident id."}
        try:
            payload = self._get(
                f"/v1/incidents/{safe_id}",
                {"include": "services,environments"},
            )
        except Exception as exc:
            return self._error("get_incident", exc)

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return {"success": False, "error": f"Rootly returned no incident for id {incident_id}."}
        return {"success": True, "incident": _shape_incident(data, full=True)}

    def list_incident_events(
        self,
        incident_id: str,
        *,
        page_size: int | None = _DEFAULT_EVENT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Return the incident timeline oldest-first.

        Causal order is the point of a timeline: reversing it makes the model
        read the resolution before the trigger.
        """
        safe_id = safe_path_segment(incident_id)
        if safe_id is None:
            return {"success": False, "error": "incident_id is not a valid Rootly incident id."}
        size = clamp(page_size, _DEFAULT_EVENT_PAGE_SIZE, _MAX_EVENT_PAGE_SIZE)
        try:
            payload = self._get(
                f"/v1/incidents/{safe_id}/events",
                {"page[size]": size, "sort": "occurred_at"},
            )
        except Exception as exc:
            return self._error("list_incident_events", exc)

        events = [_shape_event(item) for item in data_list(payload)]
        total = meta_total(payload)
        return {
            "success": True,
            "incident_id": incident_id,
            "events": events,
            "returned": len(events),
            "total": total if total is not None else len(events),
            "truncated": bool(total is not None and total > len(events)),
        }

    def post_timeline_event(
        self,
        incident_id: str,
        *,
        event: str,
        visibility: str = _INTERNAL_VISIBILITY,
    ) -> dict[str, Any]:
        """Append one event to an incident timeline.

        The occurred-at time is deliberately omitted so Rootly stamps it at
        creation — a findings note belongs at the moment it was written, not at
        a time the model guessed.
        """
        safe_id = safe_path_segment(incident_id)
        if safe_id is None:
            return {"success": False, "error": "incident_id is not a valid Rootly incident id."}
        text = (event or "").strip()
        if not text:
            return {"success": False, "error": "event text is required."}
        resolved_visibility = normalize_visibility(visibility)
        body = {
            "data": {
                "type": "incident_events",
                "attributes": {"event": text, "visibility": resolved_visibility},
            }
        }
        try:
            response = self._get_client().post(f"/v1/incidents/{safe_id}/events", json=body)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return self._error("post_timeline_event", exc)

        data = payload.get("data") if isinstance(payload, dict) else None
        created = _shape_event(data) if isinstance(data, dict) else {}
        return {
            "success": True,
            "incident_id": incident_id,
            "event_id": created.get("id", ""),
            "event": created.get("event", text),
            "visibility": created.get("visibility", resolved_visibility),
            "occurred_at": created.get("occurred_at", ""),
        }

    def _on_call_error(self, method: str, exc: Exception) -> dict[str, Any]:
        """Handle on-call specific errors with entitlement degradation."""
        if isinstance(exc, httpx.HTTPStatusError):
            # Compare the raw int: HTTPStatus(...) raises ValueError on a
            # non-standard code (Cloudflare fronts Rootly and emits 5xx codes
            # outside the enum), which would escape this error handler.
            status = int(exc.response.status_code)
            if status in ON_CALL_UNENTITLED_STATUSES:
                logger.info(
                    "[rootly] On-call access denied (HTTP %s) - account may lack On-Call product",
                    status,
                )
                return {
                    "success": False,
                    "entitled": False,
                    "error": on_call_entitlement_error(status),
                }
        # Fall back to generic error handling
        return self._error(method, exc)

    def list_on_call(
        self,
        *,
        schedule_id: str = "",
        escalation_policy_id: str = "",
        since: str = "",
        until: str = "",
        limit: int | None = 20,
        include_users: bool = True,
    ) -> dict[str, Any]:
        """List who is currently on-call with optional filtering."""
        params: dict[str, Any] = {}

        if include_users:
            params["include"] = "user"
        if schedule_id:
            params["filter[schedule_ids]"] = schedule_id
        if escalation_policy_id:
            params["filter[escalation_policy_ids]"] = escalation_policy_id
        if since:
            params["since"] = since
        if until:
            params["until"] = until

        try:
            payload = self._get(ON_CALL_PATH, params)
        except Exception as exc:
            return self._on_call_error("list_on_call", exc)

        on_call_data = data_list(payload)
        users = index_included_users(payload) if include_users else {}

        # /v1/oncalls has no server paging and no meta, so the limit is applied
        # client-side and `total` is what Rootly actually handed back.
        total = len(on_call_data)
        size = clamp(limit, _DEFAULT_ON_CALL_LIMIT, _MAX_ON_CALL_LIMIT)
        on_call = [shape_on_call(item, users) for item in on_call_data[:size]]

        return {
            "success": True,
            "on_call": on_call,
            "returned": len(on_call),
            "total": total,
            "truncated": total > len(on_call),
        }

    def list_on_call_schedules(
        self,
        *,
        search: str = "",
        limit: int | None = 20,
    ) -> dict[str, Any]:
        """List on-call schedules with optional search filtering."""
        size = clamp(limit, _DEFAULT_ON_CALL_LIMIT, _MAX_ON_CALL_LIMIT)
        params: dict[str, Any] = {"page[size]": size}

        if search:
            params["filter[search]"] = search

        try:
            payload = self._get(SCHEDULES_PATH, params)
        except Exception as exc:
            return self._on_call_error("list_on_call_schedules", exc)

        schedules = [shape_schedule(item) for item in data_list(payload)]
        total = meta_total(payload)

        return {
            "success": True,
            "schedules": schedules,
            "returned": len(schedules),
            "total": total if total is not None else len(schedules),
            "truncated": bool(total is not None and total > len(schedules)),
        }

    def list_escalation_policies(
        self,
        *,
        search: str = "",
        limit: int | None = 20,
    ) -> dict[str, Any]:
        """List escalation policies with optional search filtering."""
        size = clamp(limit, _DEFAULT_ON_CALL_LIMIT, _MAX_ON_CALL_LIMIT)
        params: dict[str, Any] = {"page[size]": size}

        if search:
            params["filter[search]"] = search

        try:
            payload = self._get(ESCALATION_POLICIES_PATH, params)
        except Exception as exc:
            return self._on_call_error("list_escalation_policies", exc)

        policies = [shape_escalation_policy(item) for item in data_list(payload)]
        total = meta_total(payload)

        return {
            "success": True,
            "escalation_policies": policies,
            "returned": len(policies),
            "total": total if total is not None else len(policies),
            "truncated": bool(total is not None and total > len(policies)),
        }

    def _alerts_error(
        self,
        method: str,
        exc: Exception,
        *,
        unentitled: frozenset[HTTPStatus],
    ) -> dict[str, Any]:
        """Degrade an Alerts entitlement gap instead of reporting a failure.

        ``capture_service_error`` classifies 403 as ``severity="error"``, so an
        account without Rootly Alerts would file one Sentry error per turn,
        forever. The statuses that mean "you do not have this product" bypass
        it; everything else — including 401, which means re-run setup rather
        than buy something — takes the normal path with telemetry.

        ``unentitled`` differs per action: see ``ALERT_GET_UNENTITLED_STATUSES``.
        """
        if isinstance(exc, httpx.HTTPStatusError):
            # Raw int, not HTTPStatus(...): Cloudflare fronts Rootly and emits
            # 520/521/524, which are not in the enum and would raise here.
            status = int(exc.response.status_code)
            if status in unentitled:
                logger.info(
                    "[rootly] Alerts access denied (HTTP %s) - account may lack the Alerts product",
                    status,
                )
                return {
                    "success": False,
                    "entitled": False,
                    "error": alerts_entitlement_error(status),
                }
        return self._error(method, exc)

    def list_alerts(
        self,
        *,
        status: str = "",
        source: str = "",
        service: str = "",
        environment: str = "",
        started_after: str = "",
        page_size: int | None = _DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List alerts newest-first, optionally filtered.

        Unlike ``/v1/oncalls``, this endpoint does page server-side, so
        ``total`` is Rootly's own count rather than the length of the slice.
        """
        size = clamp(page_size, _DEFAULT_PAGE_SIZE, _MAX_PAGE_SIZE)
        params: dict[str, Any] = {"page[size]": size, "sort": "-created_at"}
        if status:
            params["filter[status]"] = status
        if source:
            params["filter[source]"] = source
        if service:
            params["filter[services]"] = service
        if environment:
            params["filter[environments]"] = environment
        if started_after:
            params["filter[started_at][gte]"] = started_after

        try:
            payload = self._get(ALERTS_PATH, params)
        except Exception as exc:
            return self._alerts_error("list_alerts", exc, unentitled=ALERTS_UNENTITLED_STATUSES)

        alerts = [shape_alert(item) for item in data_list(payload)]
        total = meta_total(payload)
        return {
            "success": True,
            "alerts": alerts,
            "returned": len(alerts),
            "total": total if total is not None else len(alerts),
            "truncated": bool(total is not None and total > len(alerts)),
        }

    def get_alert(self, alert_id: str) -> dict[str, Any]:
        """Fetch one alert in full, including its shaped responders."""
        safe_id = safe_path_segment(alert_id)
        if safe_id is None:
            return {"success": False, "error": "alert_id is not a valid Rootly alert id."}
        try:
            payload = self._get(f"{ALERTS_PATH}/{safe_id}", {})
        except httpx.HTTPStatusError as exc:
            # A 404 here is a wrong id far more often than a missing product,
            # so it neither degrades to an entitlement message (which would
            # send someone to buy what they already own) nor files telemetry:
            # capture_service_error rates 404 as severity="error", and models
            # guess ids, so that would be one Sentry error per bad guess.
            if int(exc.response.status_code) == HTTPStatus.NOT_FOUND:
                return {"success": False, "error": f"Rootly has no alert with id {alert_id}."}
            return self._alerts_error("get_alert", exc, unentitled=ALERT_GET_UNENTITLED_STATUSES)
        except Exception as exc:
            return self._alerts_error("get_alert", exc, unentitled=ALERT_GET_UNENTITLED_STATUSES)

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return {"success": False, "error": f"Rootly returned no alert for id {alert_id}."}
        return {"success": True, "alert": shape_alert(data, full=True)}


def make_rootly_client(
    api_token: str | None,
    *,
    base_url: str = "",
    timeout_seconds: float | str | None = None,
) -> RootlyClient | None:
    """Create a Rootly client, or ``None`` when no usable token is supplied."""
    token = (api_token or "").strip()
    if not token:
        return None
    try:
        config = RootlyConfig.model_validate(
            {
                "api_token": token,
                "base_url": base_url or "",
                "timeout_seconds": timeout_seconds,
            }
        )
    except Exception as exc:
        logger.warning("[rootly] Failed to build client config: %s", exc)
        return None
    return RootlyClient(config)


__all__ = [
    "RootlyClient",
    "RootlyConfig",
    "make_rootly_client",
    "normalize_visibility",
]
