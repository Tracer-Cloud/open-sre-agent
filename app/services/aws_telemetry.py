"""AWS-specific Sentry/log tagging for service clients.

The generic ``app.utils.errors.report_exception`` is fine for one-off
``except`` sites, but every AWS client wants the same tag shape
(``service``, ``operation``, ``region``, ``error_code``, ``surface=service_client``)
and the same severity policy (vendor errors -> ``warning``, our bugs ->
``error``). Wrapping that policy here keeps the call sites short and the
Sentry signal consistent across ``aws_sdk_client.py`` and ``lambda_client.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.utils.errors import report_exception

try:
    from botocore.exceptions import ClientError
except ImportError:

    class ClientError(Exception):  # type: ignore[no-redef]
        """Stub when botocore is not installed; preserves the helper's API."""


def aws_error_code(exc: BaseException) -> str:
    """Extract the AWS error code from a botocore ``ClientError``.

    Returns ``"Unknown"`` for non-``ClientError`` exceptions or malformed
    responses, so callers can use the value as a tag without further guards.
    """
    if not isinstance(exc, ClientError):
        return "Unknown"
    return str(exc.response.get("Error", {}).get("Code", "Unknown") or "Unknown")


def report_aws_failure(
    exc: BaseException,
    *,
    logger: logging.Logger,
    message: str,
    service: str,
    operation: str,
    region: str | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Log + Sentry-capture an AWS client failure with the standard tag shape.

    ``ClientError`` is treated as a vendor problem (often expected: invalid
    credentials, missing role, quota) and reported at ``warning``. Anything
    else is treated as our own bug and reported at ``error``.
    """
    is_client_error = isinstance(exc, ClientError)
    severity = "warning" if is_client_error else "error"
    tags: dict[str, str] = {
        "surface": "service_client",
        "component": "aws",
        "service": service,
        "operation": operation,
    }
    if region:
        tags["region"] = region
    if is_client_error:
        tags["aws_error_code"] = aws_error_code(exc)
    report_exception(
        exc,
        logger=logger,
        message=message,
        severity=severity,
        tags=tags,
        extras=extras,
    )
