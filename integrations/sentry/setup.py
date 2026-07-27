"""What Sentry needs before it is considered configured.

The organization slug is required alongside the token: Sentry's API is scoped
per organization, so a token without one cannot address any endpoint. The
project slug is optional — left blank, queries span every project the token can
see.

``base_url`` moves only for self-hosted Sentry.
"""

from __future__ import annotations

from config.constants.sentry import (
    DEFAULT_SENTRY_BASE_URL,
    SENTRY_AUTH_TOKEN_ENV,
    SENTRY_BASE_URL_ENV,
    SENTRY_ORGANIZATION_SLUG_ENV,
    SENTRY_PROJECT_SLUG_ENV,
)
from integrations.sentry.verifier import verify_sentry
from integrations.setup_flow import IntegrationSetupSpec, SetupField

BASE_URL_FIELD = "base_url"
ORGANIZATION_SLUG_FIELD = "organization_slug"
AUTH_TOKEN_FIELD = "auth_token"
PROJECT_SLUG_FIELD = "project_slug"

SENTRY_SETUP = IntegrationSetupSpec(
    service="sentry",
    fields=(
        SetupField(
            name=BASE_URL_FIELD,
            label="Sentry URL",
            env_var=SENTRY_BASE_URL_ENV,
            default=DEFAULT_SENTRY_BASE_URL,
        ),
        SetupField(
            name=ORGANIZATION_SLUG_FIELD,
            label="Sentry organization slug",
            env_var=SENTRY_ORGANIZATION_SLUG_ENV,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="Sentry auth token",
            env_var=SENTRY_AUTH_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=PROJECT_SLUG_FIELD,
            label="Sentry project slug",
            prompt="Project slug (optional)",
            env_var=SENTRY_PROJECT_SLUG_ENV,
            required=False,
        ),
    ),
    verify=verify_sentry,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "BASE_URL_FIELD",
    "ORGANIZATION_SLUG_FIELD",
    "PROJECT_SLUG_FIELD",
    "SENTRY_SETUP",
]
