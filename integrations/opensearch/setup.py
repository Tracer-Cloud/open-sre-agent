"""What OpenSearch needs before it is considered configured.

Auth is a picker (basic / api_key / none). The picker scopes which fields are
asked; half-filled basic auth (a username without a password, or the reverse) is
rejected by :func:`integrations.opensearch.verifier.verify_opensearch`, so setup
and the health check agree for any surface that skips the picker. The URL is
always asked.
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
    verify=verify_opensearch,
)

__all__ = [
    "API_KEY_FIELD",
    "OPENSEARCH_SETUP",
    "PASSWORD_FIELD",
    "URL_FIELD",
    "USERNAME_FIELD",
]
