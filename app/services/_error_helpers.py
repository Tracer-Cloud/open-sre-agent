"""Shared error-telemetry helper for service-client except blocks."""

from __future__ import annotations

import logging

import httpx

from app.utils.errors import report_exception


def capture_service_error(
    exc: BaseException,
    *,
    logger: logging.Logger,
    integration: str,
    method: str,
) -> None:
    severity = "warning" if isinstance(exc, httpx.HTTPStatusError) else "error"
    report_exception(
        exc,
        logger=logger,
        message=f"[{integration}] {method} failed",
        severity=severity,
        tags={"surface": "service_client", "integration": integration},
        extras={"method": method},
    )
