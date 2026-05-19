"""Shared error reporting helpers for remote runtime fallbacks."""

from __future__ import annotations

import logging
from typing import Any

from app.utils.errors import report_exception

REMOTE_SURFACE = "remote_server"


def report_remote_exception(
    exc: BaseException,
    *,
    logger: logging.Logger,
    component: str,
    event: str,
    message: str,
    severity: str = "error",
    tags: dict[str, str] | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Report a swallowed remote-runtime exception with consistent tags."""
    report_tags = {
        "surface": REMOTE_SURFACE,
        "component": component,
        "event": event,
    }
    if tags:
        report_tags.update(tags)
    report_exception(
        exc,
        logger=logger,
        message=message,
        severity=severity,
        tags=report_tags,
        extras=extras,
    )
