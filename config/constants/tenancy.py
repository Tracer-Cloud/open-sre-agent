"""Env names the multi-tenant control plane injects into a gateway silo.

A silo is one gateway task serving one tenant. The control plane's ECS task
definition supplies these three so the gateway can say who it is and fetch that
tenant's credentials at startup.

Distinct from :data:`config.constants.billing.ORGANIZATION_ID_ENV`
(``OPENSRE_ORGANIZATION_ID``), which the product reads to attribute usage and to
enforce that a mounted context volume belongs to the organization being served.
Both name an organization, but different systems set them and neither may stand
in for the other: reading the billing name here fails hydration in a deployed
silo, and falling back to this one there would weaken a fail-closed ownership
check.
"""

from __future__ import annotations

from typing import Final

#: The tenant this silo serves, assigned by the control plane.
TENANT_ORGANIZATION_ID_ENV: Final[str] = "ORGANIZATION_ID"

#: Where the gateway exchanges its identity for the tenant's credentials.
CREDENTIALS_API_URL_ENV: Final[str] = "OPENSRE_CREDENTIALS_API_URL"

#: Secrets Manager ARN holding this tenant's bootstrap bundle.
CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV: Final[str] = "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN"

__all__ = [
    "CREDENTIALS_API_URL_ENV",
    "CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV",
    "TENANT_ORGANIZATION_ID_ENV",
]
