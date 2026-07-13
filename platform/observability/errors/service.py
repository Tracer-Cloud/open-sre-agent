"""Shared error-telemetry helper for service-client except blocks."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from platform.observability.errors.boundary import report_exception

#: HTTP status for vendor rate-limit (transient throttling, not a config error).
_HTTP_TOO_MANY_REQUESTS = 429
#: HTTP statuses at or above this are treated as transient vendor failures.
_HTTP_SERVER_ERROR_FLOOR = 500


def _is_transient_vendor_error(exc: BaseException) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    sc = exc.response.status_code
    return sc == _HTTP_TOO_MANY_REQUESTS or sc >= _HTTP_SERVER_ERROR_FLOOR


def capture_service_error(
    exc: BaseException,
    *,
    logger: logging.Logger,
    integration: str,
    method: str,
    extras: dict[str, Any] | None = None,
) -> None:
    severity = "warning" if _is_transient_vendor_error(exc) else "error"
    merged_extras: dict[str, Any] = dict(extras) if extras else {}
    merged_extras.pop("surface", None)
    merged_extras["method"] = method
    report_exception(
        exc,
        logger=logger,
        message=f"[{integration}] {method} failed",
        severity=severity,
        tags={"surface": "service_client", "integration": integration},
        extras=merged_extras,
    )
