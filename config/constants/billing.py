"""Env-var names and tunables for per-org credit metering."""

from __future__ import annotations

from typing import Final

# Injected by the org-silo infra (ECS task definition). Metering stays off
# unless all three are set.
WEBAPP_URL_ENV: Final[str] = "OPENSRE_WEBAPP_URL"
USAGE_SECRET_ENV: Final[str] = "AGENT_USAGE_SECRET"
ORGANIZATION_ID_ENV: Final[str] = "OPENSRE_ORGANIZATION_ID"

CREDITS_HTTP_TIMEOUT_SECONDS: Final[float] = 5.0
