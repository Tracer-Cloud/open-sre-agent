"""Application-wide constants."""

from __future__ import annotations

from config.constants.billing import (
    CREDITS_HTTP_TIMEOUT_SECONDS,
    ORGANIZATION_ID_ENV,
    USAGE_SECRET_ENV,
    WEBAPP_URL_ENV,
)
from config.constants.coralogix import (
    CORALOGIX_API_KEY_ENV,
    CORALOGIX_APPLICATION_NAME_ENV,
    CORALOGIX_BASE_URL_ENV,
    CORALOGIX_SUBSYSTEM_NAME_ENV,
)
from config.constants.datadog import (
    DATADOG_API_KEY_ENV,
    DATADOG_APP_KEY_ENV,
    DATADOG_SITE_ENV,
)
from config.constants.honeycomb import (
    HONEYCOMB_API_KEY_ENV,
    HONEYCOMB_BASE_URL_ENV,
    HONEYCOMB_DATASET_ENV,
)
from config.constants.investigation import MAX_INVESTIGATION_LOOPS
from config.constants.llm import (
    AZURE_OPENAI_API_KEY_ENV,
    AZURE_OPENAI_API_VERSION_ENV,
    AZURE_OPENAI_BASE_URL_ENV,
)
from config.constants.paths import (
    INTEGRATIONS_STORE_PATH,
    OPENSRE_HOME_DIR,
    OPENSRE_TMP_DIR,
    ensure_opensre_tmp_dir,
    get_store_path,
)
from config.constants.platform import IS_WINDOWS
from config.constants.posthog import (
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
from config.constants.telegram import (
    TELEGRAM_BOT_TOKEN_ENV,
    TELEGRAM_DEFAULT_CHAT_ID_ENV,
)

__all__ = [
    "AZURE_OPENAI_API_KEY_ENV",
    "AZURE_OPENAI_API_VERSION_ENV",
    "AZURE_OPENAI_BASE_URL_ENV",
    "CORALOGIX_API_KEY_ENV",
    "CORALOGIX_APPLICATION_NAME_ENV",
    "CORALOGIX_BASE_URL_ENV",
    "CORALOGIX_SUBSYSTEM_NAME_ENV",
    "CREDITS_HTTP_TIMEOUT_SECONDS",
    "DATADOG_API_KEY_ENV",
    "DATADOG_APP_KEY_ENV",
    "DATADOG_SITE_ENV",
    "DEFAULT_POSTHOG_TIMEOUT_SECONDS",
    "DEFAULT_POSTHOG_URL",
    "HONEYCOMB_API_KEY_ENV",
    "HONEYCOMB_BASE_URL_ENV",
    "HONEYCOMB_DATASET_ENV",
    "INTEGRATIONS_STORE_PATH",
    "IS_WINDOWS",
    "MAX_INVESTIGATION_LOOPS",
    "OPENSRE_HOME_DIR",
    "OPENSRE_TMP_DIR",
    "ORGANIZATION_ID_ENV",
    "POSTHOG_CAPTURE_API_KEY",
    "POSTHOG_HOST",
    "SENTRY_DSN",
    "SENTRY_ERROR_SAMPLE_RATE",
    "SENTRY_IN_APP_INCLUDE",
    "SENTRY_MAX_BREADCRUMBS",
    "SENTRY_TRACES_SAMPLE_RATE",
    "TELEGRAM_BOT_TOKEN_ENV",
    "TELEGRAM_DEFAULT_CHAT_ID_ENV",
    "USAGE_SECRET_ENV",
    "WEBAPP_URL_ENV",
    "ensure_opensre_tmp_dir",
    "get_store_path",
]
