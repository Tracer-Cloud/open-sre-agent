"""What Grafana Tempo needs before it is considered configured.

Tempo commonly runs unauthenticated behind a gateway, or with a bearer token,
or with basic auth — every field but ``url`` is independently optional, and
nothing enforces "bearer token XOR username/password" beyond the prompt text;
the connectivity probe (``verify_tempo``) is what actually rejects a broken
combination.
"""

from __future__ import annotations

from config.constants.tempo import (
    TEMPO_API_KEY_ENV,
    TEMPO_ORG_ID_ENV,
    TEMPO_PASSWORD_ENV,
    TEMPO_URL_ENV,
    TEMPO_USERNAME_ENV,
)
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.tempo.verifier import verify_tempo

URL_FIELD = "url"
API_KEY_FIELD = "api_key"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
ORG_ID_FIELD = "org_id"

TEMPO_SETUP = IntegrationSetupSpec(
    service="tempo",
    fields=(
        SetupField(
            name=URL_FIELD,
            label="Tempo URL",
            prompt="Tempo URL (e.g. http://localhost:3200 for local Docker)",
            env_var=TEMPO_URL_ENV,
        ),
        SetupField(
            name=API_KEY_FIELD,
            label="Tempo bearer token",
            prompt="Tempo bearer token (optional, leave blank if using basic auth or none)",
            env_var=TEMPO_API_KEY_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Tempo username",
            prompt="Tempo username (optional, for basic auth)",
            env_var=TEMPO_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Tempo password",
            prompt="Tempo password (optional, for basic auth)",
            env_var=TEMPO_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=ORG_ID_FIELD,
            label="Tempo tenant / X-Scope-OrgID",
            prompt="Tempo tenant / X-Scope-OrgID (optional, leave blank if single-tenant)",
            env_var=TEMPO_ORG_ID_ENV,
            required=False,
        ),
    ),
    verify=verify_tempo,
)

__all__ = [
    "API_KEY_FIELD",
    "ORG_ID_FIELD",
    "PASSWORD_FIELD",
    "TEMPO_SETUP",
    "URL_FIELD",
    "USERNAME_FIELD",
]
