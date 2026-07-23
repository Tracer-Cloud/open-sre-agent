"""Env-var names and tunables for per-org credit metering."""

from __future__ import annotations

from typing import Final

# Injected by the org-silo infra (ECS task definition). Metering/vault stay off
# unless the webapp URL, an auth token, and the org id are all set.
WEBAPP_URL_ENV: Final[str] = "OPENSRE_WEBAPP_URL"
ORGANIZATION_ID_ENV: Final[str] = "OPENSRE_ORGANIZATION_ID"

# Silo → webapp auth. The silo mints a short-lived M2M token (`mt_…`) from its
# Clerk machine secret key; the webapp verifies that token against the org's
# bound machine, so it is org-scoped and cannot act as another tenant. The
# shared secret is the fallback credential, accepted by the webapp only while
# its own env var is set.
MACHINE_SECRET_ENV: Final[str] = "CLERK_MACHINE_SECRET_KEY"
USAGE_SECRET_ENV: Final[str] = "AGENT_USAGE_SECRET"

# Clerk Backend API base for minting M2M tokens; override for testing/self-host.
CLERK_API_BASE_URL_ENV: Final[str] = "CLERK_API_BASE_URL"
CLERK_API_BASE_URL_DEFAULT: Final[str] = "https://api.clerk.com"

CREDITS_HTTP_TIMEOUT_SECONDS: Final[float] = 5.0

# Minted M2M tokens are short-lived and cached in-process; refresh slightly
# before expiry so a token can't lapse mid-request.
MACHINE_TOKEN_TTL_SECONDS: Final[int] = 3600
MACHINE_TOKEN_REFRESH_MARGIN_SECONDS: Final[int] = 60
