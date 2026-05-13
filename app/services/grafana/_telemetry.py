"""Grafana service-client error telemetry."""

from __future__ import annotations

import logging
from typing import Any

import requests

from app.utils.errors import report_exception

logger = logging.getLogger(__name__)


def _status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def report_grafana_failure(
    exc: BaseException,
    *,
    component: str,
    method: str,
    datasource_uid: str | None = None,
    severity: str | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Report a Grafana soft-fail path with consistent tags."""
    status_code = _status_code(exc)
    likely_config_or_network = isinstance(exc, requests.RequestException) and (
        status_code is None or 400 <= status_code < 500
    )
    effective_severity = severity or ("warning" if likely_config_or_network else "error")
    tags = {
        "surface": "service_client",
        "integration": "grafana",
        "component": f"app.services.grafana.{component}",
        "method": method,
    }
    if datasource_uid:
        tags["datasource_uid"] = datasource_uid
    if status_code is not None:
        tags["status_code"] = str(status_code)

    report_exception(
        exc,
        logger=logger,
        message=f"[grafana] {component}.{method} failed",
        severity=effective_severity,
        tags=tags,
        extras=extras,
    )


__all__ = ["report_grafana_failure"]
