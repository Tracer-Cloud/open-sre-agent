"""Shared error-reporting helpers for boundary exception handling.

Use these helpers at every ``except`` site that intentionally swallows or
re-raises an exception, so the failure is always visible in Sentry and logs.

Tagging conventions (pass via ``tags``):
  surface   — cli | interactive_shell | service_client | tool | node |
               pipeline | integration | remote_server | analytics | auth |
               webapp | mcp | sandbox | deployment | masking
  component — module-level identifier, e.g. ``app.services.grafana.tempo``
  integration — vendor when applicable: grafana | splunk | vercel | …
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.utils.sentry_sdk import capture_exception


def report_exception(
    exc: BaseException,
    *,
    logger: logging.Logger,
    message: str,
    severity: str = "error",
    tags: dict[str, str] | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Log + Sentry-capture an exception with structured context.

    Use at boundaries where an exception is intentionally swallowed (the
    function returns a degraded value to its caller).
    """
    log_fn = getattr(logger, severity, logger.error)
    log_fn("%s", message, exc_info=exc)
    combined: dict[str, Any] = {}
    if tags:
        combined.update({f"tag.{k}": v for k, v in tags.items()})
    if extras:
        combined.update(extras)
    capture_exception(exc, extra=combined or None)


@contextmanager
def report_and_swallow(
    *,
    logger: logging.Logger,
    message: str,
    tags: dict[str, str] | None = None,
    extras: dict[str, Any] | None = None,
    swallow: type[BaseException] | tuple[type[BaseException], ...] = Exception,
) -> Iterator[None]:
    """Context manager that logs + reports a matching exception, then swallows it.

    Replaces bare ``try/except Exception: pass`` patterns where the caller does
    not need the value but does want the failure visible in Sentry.
    """
    try:
        yield
    except swallow as exc:
        report_exception(exc, logger=logger, message=message, tags=tags, extras=extras)


@contextmanager
def report_and_reraise(
    *,
    logger: logging.Logger,
    message: str,
    tags: dict[str, str] | None = None,
    extras: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Context manager that captures to Sentry + re-raises (for top-level boundaries)."""
    try:
        yield
    except Exception as exc:
        report_exception(exc, logger=logger, message=message, tags=tags, extras=extras)
        raise


class OpenSRESilentFallback(Warning):
    """Warning class for debug-only fallback paths that should still reach Sentry."""
