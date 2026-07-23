"""What OpenSearch needs before it is considered configured.

Auth used to be a branching select (basic / api_key / none). Every auth field is
independently optional — same shape as Tempo — but half-filled basic auth is
rejected early: a username without a password (or the reverse) would otherwise
persist and send unauthenticated requests against a secured cluster.
"""

from __future__ import annotations

from config.constants.opensearch import (
    OPENSEARCH_API_KEY_ENV,
    OPENSEARCH_PASSWORD_ENV,
    OPENSEARCH_URL_ENV,
    OPENSEARCH_USERNAME_ENV,
)
from integrations.opensearch.verifier import verify_opensearch
from integrations.setup_flow import IntegrationSetupSpec, SetupField

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
            prompt="API key (optional, leave blank if using basic auth or none)",
            env_var=OPENSEARCH_API_KEY_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            prompt="Username (optional, for basic auth)",
            env_var=OPENSEARCH_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            prompt="Password (optional, for basic auth)",
            env_var=OPENSEARCH_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
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
