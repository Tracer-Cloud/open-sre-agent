"""Shared error-telemetry helper for service-client except blocks."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.utils.errors import report_exception


def _is_transient_vendor_error(exc: BaseException) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    sc = exc.response.status_code
    # 429 = vendor rate-limit (transient throttling, not a config error)
    return sc == 429 or sc >= 500


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
