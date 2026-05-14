"""AWS-specific Sentry routing for boto3 / botocore failures (#1462).

PR #1922 standardised httpx-based service-client telemetry via
``capture_service_error``. Two boto3-based modules — ``aws_sdk_client.py``
and ``lambda_client.py`` — were intentionally scoped out because their
exception hierarchy is different (no ``httpx.HTTPStatusError``, no
``status_code`` attribute).

This module mirrors that helper for the boto3 family:

  * ``botocore.exceptions.ClientError`` →  severity=warning, tag the AWS
    error code (``AccessDeniedException``, ``ThrottlingException``,
    ``ResourceNotFoundException``, ...). The 5xx / 429 split also folds in:
    transient AWS failures stay at warning even when not raised as
    ``ClientError``.
  * ``NoCredentialsError`` / ``ParamValidationError`` → severity=warning,
    user-config issue.
  * Anything else → severity=error, our bug.

Captured events carry::

    surface     = service_client
    integration = aws
    component   = app.services.<module>
    service     = ec2 | s3 | lambda | sts | ...    (when known)
    operation   = describe_instances | ...         (when known)
    error_code  = <botocore code>                  (ClientError only)
    status_code = 4xx / 5xx                        (ClientError only)
    event       = aws_call_failed | aws_extract_failed | aws_decode_failed

When ``app/services/_base.py`` lands from #1458, this can be folded into
the shared service-client helper. Keeping it standalone for now avoids a
forking dependency.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from botocore.exceptions import (
        ClientError,
        NoCredentialsError,
        ParamValidationError,
    )
except ImportError:  # pragma: no cover - botocore is a runtime dep but be defensive
    ClientError = NoCredentialsError = ParamValidationError = ()  # type: ignore[assignment]

from app.utils.errors import report_exception


def _extract_client_error_metadata(exc: BaseException) -> dict[str, str]:
    """Pull the AWS error code and HTTP status code off a ``ClientError``."""
    response: Any = getattr(exc, "response", None) or {}
    err = response.get("Error", {}) if isinstance(response, dict) else {}
    meta = response.get("ResponseMetadata", {}) if isinstance(response, dict) else {}
    tags: dict[str, str] = {}
    code = err.get("Code") if isinstance(err, dict) else None
    if code:
        tags["error_code"] = str(code)
    status = meta.get("HTTPStatusCode") if isinstance(meta, dict) else None
    if status:
        tags["status_code"] = str(status)
    return tags


def _severity_for(exc: BaseException) -> str:
    if not ClientError:
        return "error"
    if isinstance(exc, ClientError):
        # AWS-side: throttling, transient 5xx, access-denied — all caller-actionable
        # or transient, not our bug. Warning lets these be sampled distinctly
        # from real internal errors.
        return "warning"
    if isinstance(exc, NoCredentialsError | ParamValidationError):
        return "warning"
    return "error"


def capture_aws_error(
    exc: BaseException,
    *,
    component: str,
    service: str | None = None,
    operation: str | None = None,
    event: str = "aws_call_failed",
    logger: logging.Logger,
    extra_tags: dict[str, str] | None = None,
) -> None:
    """Route a boto3 / botocore exception to Sentry with AWS-aware tags.

    Caller still owns the response shape (callers return their existing
    error dict to preserve the historic contract); this helper only adds
    the missing Sentry capture.
    """
    tags: dict[str, str] = {
        "surface": "service_client",
        "integration": "aws",
        "component": component,
        "event": event,
    }
    if service:
        tags["service"] = service
    if operation:
        tags["operation"] = operation
    if ClientError and isinstance(exc, ClientError):
        tags.update(_extract_client_error_metadata(exc))
    if extra_tags:
        tags.update(extra_tags)

    severity = _severity_for(exc)
    message = f"AWS call failed: component={component}"
    if service:
        message += f" service={service}"
    if operation:
        message += f" operation={operation}"

    report_exception(
        exc,
        logger=logger,
        message=message,
        severity=severity,
        tags=tags,
    )
