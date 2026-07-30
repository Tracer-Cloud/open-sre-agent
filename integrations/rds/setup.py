"""What RDS needs before it is considered configured.

The old CLI collected host/port/database/username/password, but runtime tools
and the catalog only read ``db_instance_identifier`` and ``region`` (AWS Describe
APIs, not a SQL connection). This spec matches what the tools actually use.
"""

from __future__ import annotations

from config.constants.rds import RDS_DB_INSTANCE_IDENTIFIER_ENV, RDS_REGION_ENV
from integrations.rds import DEFAULT_RDS_REGION
from integrations.setup_flow import IntegrationSetupSpec, SetupField

DB_INSTANCE_IDENTIFIER_FIELD = "db_instance_identifier"
REGION_FIELD = "region"

RDS_SETUP = IntegrationSetupSpec(
    service="rds",
    fields=(
        SetupField(
            name=DB_INSTANCE_IDENTIFIER_FIELD,
            label="DB instance identifier",
            prompt="RDS DB instance identifier (e.g. checkout-prod)",
            env_var=RDS_DB_INSTANCE_IDENTIFIER_ENV,
        ),
        SetupField(
            name=REGION_FIELD,
            label="AWS region",
            prompt="AWS region (e.g. us-east-1)",
            env_var=RDS_REGION_ENV,
            default=DEFAULT_RDS_REGION,
        ),
    ),
    verify=None,
)

__all__ = [
    "DB_INSTANCE_IDENTIFIER_FIELD",
    "RDS_SETUP",
    "REGION_FIELD",
]
