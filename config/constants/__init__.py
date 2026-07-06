"""Application-wide constants."""

from __future__ import annotations

from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from config.constants.paths import (
    INTEGRATIONS_STORE_PATH,
    OPENSRE_HOME_DIR,
    OPENSRE_TMP_DIR,
    ensure_opensre_tmp_dir,
    get_store_path,
)
from config.constants.platform import IS_WINDOWS
from config.constants.posthog import (
    DEFAULT_POSTHOG_BOUNCE_THRESHOLD,
    DEFAULT_POSTHOG_BOUNCE_WINDOW,
    DEFAULT_POSTHOG_TIMEOUT_SECONDS,
    DEFAULT_POSTHOG_URL,
    POSTHOG_CAPTURE_API_KEY,
    POSTHOG_HOST,
)
from config.constants.sentry import (
    SENTRY_DSN,
    SENTRY_ERROR_SAMPLE_RATE,
    SENTRY_IN_APP_INCLUDE,
    SENTRY_MAX_BREADCRUMBS,
    SENTRY_TRACES_SAMPLE_RATE,
)

__all__ = [
    "DEFAULT_POSTHOG_BOUNCE_THRESHOLD",
    "DEFAULT_POSTHOG_BOUNCE_WINDOW",
    "DEFAULT_POSTHOG_TIMEOUT_SECONDS",
    "DEFAULT_POSTHOG_URL",
    "INTEGRATIONS_STORE_PATH",
    "IS_WINDOWS",
    "MAX_INVESTIGATION_LOOPS",
    "OPENSRE_HOME_DIR",
    "OPENSRE_TMP_DIR",
    "POSTHOG_CAPTURE_API_KEY",
    "POSTHOG_HOST",
    "SENTRY_DSN",
    "SENTRY_ERROR_SAMPLE_RATE",
    "SENTRY_IN_APP_INCLUDE",
    "SENTRY_MAX_BREADCRUMBS",
    "SENTRY_TRACES_SAMPLE_RATE",
    "ensure_opensre_tmp_dir",
    "get_store_path",
]
