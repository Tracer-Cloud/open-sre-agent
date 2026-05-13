"""AWS-specific service-client error telemetry."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.errors import report_exception

try:
    from botocore.exceptions import ClientError
except ImportError:

    class ClientError(Exception):  # type: ignore[no-redef]
        """Stub when botocore is not installed."""


logger = logging.getLogger(__name__)


def aws_error_code(exc: BaseException) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "Unknown"))
    return type(exc).__name__


def report_aws_service_exception(
    exc: BaseException,
    *,
    service: str,
    operation: str,
    region: str | None = None,
    function_name: str | None = None,
    severity: str | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Report an AWS service-client exception with consistent tags."""
    error_code = aws_error_code(exc)
    effective_severity = severity or ("warning" if isinstance(exc, ClientError) else "error")
    tags = {
        "surface": "service_client",
        "cloud": "aws",
        "service": service,
        "operation": operation,
        "error_code": error_code,
    }
    if region:
        tags["region"] = region
    if function_name:
        tags["function_name"] = function_name
    report_exception(
        exc,
        logger=logger,
        message=f"[aws] {service}.{operation} failed: {error_code}",
        severity=effective_severity,
        tags=tags,
        extras=extras,
    )


__all__ = ["aws_error_code", "report_aws_service_exception"]
