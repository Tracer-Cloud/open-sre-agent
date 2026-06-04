"""Shared PostHog constants used across analytics and integrations."""

from __future__ import annotations

import os
from typing import Final

POSTHOG_HOST: Final[str] = "https://us.i.posthog.com"
POSTHOG_CAPTURE_API_KEY_ENV: Final[str] = "POSTHOG_CAPTURE_API_KEY"
# Kept for import compatibility; use posthog_capture_api_key() for the live key.
POSTHOG_CAPTURE_API_KEY: Final[str] = ""


def posthog_capture_api_key() -> str:
    """Return the optional PostHog capture key from the process environment."""
    return os.getenv(POSTHOG_CAPTURE_API_KEY_ENV, "").strip()


DEFAULT_POSTHOG_URL: Final[str] = POSTHOG_HOST
DEFAULT_POSTHOG_TIMEOUT_SECONDS: Final[float] = 15.0
DEFAULT_POSTHOG_BOUNCE_THRESHOLD: Final[float] = 0.6
DEFAULT_POSTHOG_BOUNCE_WINDOW: Final[str] = "24h"
