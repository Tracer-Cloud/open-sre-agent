"""Sentry constants for OpenSRE runtime error monitoring."""

from __future__ import annotations

from typing import Final

SENTRY_DSN: Final[str] = (
    "https://06d6b2b739eb2267864d12c6cad34e70"
    "@o4509281671380992.ingest.us.sentry.io/4511150863482880"
)
SENTRY_ERROR_SAMPLE_RATE: Final[float] = 0.2
SENTRY_TRACES_SAMPLE_RATE: Final[float] = 0.2
