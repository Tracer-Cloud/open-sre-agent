"""What Vercel needs before it is considered configured.

``team_id`` is optional: personal accounts have none, and supplying one scopes
every query to that team.
"""

from __future__ import annotations

from config.constants.vercel import VERCEL_API_TOKEN_ENV, VERCEL_TEAM_ID_ENV
from integrations.setup_flow import IntegrationSetupSpec, SetupField
from integrations.vercel.verifier import verify_vercel

API_TOKEN_FIELD = "api_token"
TEAM_ID_FIELD = "team_id"

VERCEL_SETUP = IntegrationSetupSpec(
    service="vercel",
    fields=(
        SetupField(
            name=API_TOKEN_FIELD,
            label="Vercel API token",
            env_var=VERCEL_API_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=TEAM_ID_FIELD,
            label="Vercel team ID",
            prompt="Team ID (optional for personal accounts)",
            env_var=VERCEL_TEAM_ID_ENV,
            required=False,
        ),
    ),
    verify=verify_vercel,
)

__all__ = [
    "API_TOKEN_FIELD",
    "TEAM_ID_FIELD",
    "VERCEL_SETUP",
]
