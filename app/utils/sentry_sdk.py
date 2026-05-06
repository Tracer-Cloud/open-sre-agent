"""Sentry SDK initialisation for runtime error monitoring.

Initialises Sentry using the project DSN constant.  Call ``init_sentry()`` once
early in each process entry-point (CLI, LangGraph worker, etc.).  Repeated calls
are safe — the function is idempotent.
"""

from __future__ import annotations

import os
from contextlib import suppress
from functools import cache

from app.constants import (
    SENTRY_DSN,
    SENTRY_ERROR_SAMPLE_RATE,
    SENTRY_TRACES_SAMPLE_RATE,
)


def _is_sentry_disabled() -> bool:
    return (
        os.getenv("OPENSRE_NO_TELEMETRY", "0") == "1"
        or os.getenv("OPENSRE_SENTRY_DISABLED", "0") == "1"
        or os.getenv("DO_NOT_TRACK", "0") == "1"
    )


def _sample_rate_from_env(env_var: str, default: float) -> float:
    try:
        sample_rate = float(os.getenv(env_var, str(default)))
    except ValueError:
        return default
    return min(1.0, max(0.0, sample_rate))


def _dsn_from_env() -> str:
    """Use the project DSN by default while allowing operator-side rotation."""
    return (
        os.getenv("OPENSRE_SENTRY_DSN", "").strip()
        or os.getenv("SENTRY_DSN", "").strip()
        or SENTRY_DSN
    )


@cache
def _init_sentry_once(
    dsn: str,
    environment: str,
    release: str,
    sample_rate: float,
    traces_sample_rate: float,
) -> None:
    """Initialize Sentry once per effective runtime configuration."""
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=False,
        attach_stacktrace=True,
        sample_rate=sample_rate,
        traces_sample_rate=traces_sample_rate,
    )


def init_sentry() -> None:
    """Configure and start the Sentry SDK if a DSN is available.

    Sentry uses the project DSN constant by default so packaged builds capture
    errors without requiring per-host configuration. Set ``OPENSRE_SENTRY_DSN``
    or ``SENTRY_DSN`` to override the destination, and set
    ``OPENSRE_SENTRY_DISABLED=1``, ``OPENSRE_NO_TELEMETRY=1``, or
    ``DO_NOT_TRACK=1`` to opt out.
    """
    if _is_sentry_disabled():
        return

    from app.config import get_environment
    from app.version import get_version

    _init_sentry_once(
        dsn=_dsn_from_env(),
        environment=get_environment().value,
        release=f"opensre@{get_version()}",
        sample_rate=_sample_rate_from_env(
            "SENTRY_ERROR_SAMPLE_RATE",
            SENTRY_ERROR_SAMPLE_RATE,
        ),
        traces_sample_rate=_sample_rate_from_env(
            "SENTRY_TRACES_SAMPLE_RATE",
            SENTRY_TRACES_SAMPLE_RATE,
        ),
    )


def capture_exception(exc: BaseException) -> None:
    """Best-effort capture for exceptions swallowed by boundary adapters."""
    if _is_sentry_disabled():
        return
    with suppress(Exception):
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
