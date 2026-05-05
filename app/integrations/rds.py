"""Shared AWS RDS integration helpers.

Provides configuration normalization, source detection, and parameter
extraction for the RDS investigation tools. All AWS API calls are
read-only and routed through the shared aws_sdk_client allowlist.
"""

from __future__ import annotations

from typing import Any

from app.integrations._relational import env_str
from app.strict_config import StrictConfigModel

DEFAULT_RDS_REGION = "us-east-1"


class RDSConfig(StrictConfigModel):
    """Normalized RDS connection settings."""

    db_instance_identifier: str = ""
    region: str = DEFAULT_RDS_REGION
    integration_id: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.db_instance_identifier and self.region)


def build_rds_config(raw: dict[str, Any] | None) -> RDSConfig:
    """Build a normalized RDS config object from env/store data."""
    return RDSConfig.model_validate(raw or {})


def rds_config_from_env() -> RDSConfig | None:
    """Load an RDS config from env vars."""
    db_id = env_str("RDS_DB_INSTANCE_IDENTIFIER")
    if not db_id:
        return None
    return build_rds_config(
        {
            "db_instance_identifier": db_id,
            "region": env_str("AWS_REGION") or env_str("RDS_REGION") or DEFAULT_RDS_REGION,
        }
    )


def rds_is_available(sources: dict[str, dict]) -> bool:
    """Check if RDS integration identifying params are present."""
    rds = sources.get("rds", {})
    return bool(rds.get("db_instance_identifier"))


def rds_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract RDS identifying params (db_instance_identifier, region)."""
    rds = sources.get("rds", {})
    region = (
        str(rds.get("region") or "").strip()
        or env_str("AWS_REGION")
        or env_str("RDS_REGION")
        or DEFAULT_RDS_REGION
    )
    return {
        "db_instance_identifier": str(rds.get("db_instance_identifier", "")).strip(),
        "region": region,
    }
