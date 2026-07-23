"""What OpenSearch needs before it is considered configured.

Auth is a picker (basic / api_key / none). The picker scopes which fields are
asked; ``validate`` still rejects half-filled basic auth (a username without a
password, or the reverse) for any collection surface that skips the picker,
since that would persist and send unauthenticated requests against a secured
cluster. The URL is always asked.
"""

from __future__ import annotations

from config.constants.opensearch import (
    OPENSEARCH_API_KEY_ENV,
    OPENSEARCH_PASSWORD_ENV,
    OPENSEARCH_URL_ENV,
    OPENSEARCH_USERNAME_ENV,
)
from integrations.opensearch.verifier import verify_opensearch
from integrations.setup_flow import IntegrationSetupSpec, SetupField, SetupMode

URL_FIELD = "url"
API_KEY_FIELD = "api_key"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"


def _reject_incomplete_basic(credentials: dict[str, str | None]) -> str:
    """Require both basic-auth halves, or neither."""
    user = credentials.get(USERNAME_FIELD)
    password = credentials.get(PASSWORD_FIELD)
    if bool(user) != bool(password):
        return "Provide both username and password for basic auth, or leave both blank."
    return ""


OPENSEARCH_SETUP = IntegrationSetupSpec(
    service="opensearch",
    fields=(
        SetupField(
            name=URL_FIELD,
            label="OpenSearch URL",
            prompt="URL (e.g. https://my-cluster.us-east-1.es.amazonaws.com)",
            env_var=OPENSEARCH_URL_ENV,
        ),
        SetupField(
            name=API_KEY_FIELD,
            label="API key",
            prompt="API key",
            env_var=OPENSEARCH_API_KEY_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            prompt="Username",
            env_var=OPENSEARCH_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            prompt="Password",
            env_var=OPENSEARCH_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
    ),
    mode_prompt="OpenSearch authentication method:",
    modes=(
        SetupMode(
            value="basic",
            label="Username + Password (HTTP Basic Auth)",
            fields=(USERNAME_FIELD, PASSWORD_FIELD),
        ),
        SetupMode(value="api_key", label="API key", fields=(API_KEY_FIELD,)),
        SetupMode(value="none", label="None (security disabled)"),
    ),
    validate=_reject_incomplete_basic,
    verify=verify_opensearch,
)

__all__ = [
    "API_KEY_FIELD",
    "OPENSEARCH_SETUP",
    "PASSWORD_FIELD",
    "URL_FIELD",
    "USERNAME_FIELD",
]
