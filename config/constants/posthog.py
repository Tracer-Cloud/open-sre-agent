"""PostHog constants — OpenSRE's own product analytics, and the PostHog integration.

Two unrelated things share the vendor name. ``POSTHOG_CAPTURE_API_KEY`` is a
write-only key for *OpenSRE's* analytics project; the ``*_ENV`` names further
down identify the credentials a *user* supplies to let investigations query
*their* PostHog. Nothing is shared between them but the default host.
"""

from __future__ import annotations

from typing import Final

POSTHOG_HOST: Final[str] = "https://us.i.posthog.com"
POSTHOG_CAPTURE_API_KEY: Final[str] = "phc_zutpVhmQw7oUmMkbawKNdYCKQWjpfASATtf5ywB75W2"

DEFAULT_POSTHOG_URL: Final[str] = POSTHOG_HOST
DEFAULT_POSTHOG_TIMEOUT_SECONDS: Final[float] = 15.0

# --- The user's PostHog integration ---------------------------------------
POSTHOG_BASE_URL_ENV: Final[str] = "POSTHOG_BASE_URL"
POSTHOG_PROJECT_ID_ENV: Final[str] = "POSTHOG_PROJECT_ID"
POSTHOG_PERSONAL_API_KEY_ENV: Final[str] = "POSTHOG_PERSONAL_API_KEY"
POSTHOG_TIMEOUT_SECONDS_ENV: Final[str] = "POSTHOG_TIMEOUT_SECONDS"
