"""RDS instance description tool — backed by aws_sdk_client."""

from __future__ import annotations

import logging
from typing import Any

from app.integrations.rds import (
    DEFAULT_RDS_REGION,
    rds_extract_params,
    rds_is_available,
)
from app.services.aws_sdk_client import execute_aws_sdk_call
from app.tools.tool_decorator import tool

logger = logging.getLogger(__name__)


@tool(
    name="describe_rds_instance",
    source="rds",
    description=(
        "Describe an AWS RDS database instance — engine, version, status, "
        "storage, Multi-AZ, endpoint, and parameter groups."
    ),
    use_cases=[
        "Investigating instance-level issues: status, availability, engine version",
        "Checking if Multi-AZ is enabled or storage is misconfigured",
        "Verifying RDS instance status (available, modifying, failed)",
    ],
    requires=["db_instance_identifier"],
    input_schema={
        "type": "object",
        "properties": {
            "db_instance_identifier": {"type": "string"},
            "region": {"type": "string", "default": DEFAULT_RDS_REGION},
        },
        "required": ["db_instance_identifier"],
    },
    is_available=rds_is_available,
    extract_params=rds_extract_params,
)
def describe_rds_instance(
    db_instance_identifier: str,
    region: str = DEFAULT_RDS_REGION,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Describe an RDS instance — status, engine, storage, networking."""
    logger.info(
        "[rds] describe_rds_instance db=%s region=%s",
        db_instance_identifier,
        region,
    )

    result = execute_aws_sdk_call(
        service_name="rds",
        operation_name="describe_db_instances",
        parameters={"DBInstanceIdentifier": db_instance_identifier},
        region=region,
    )

    if not result.get("success"):
        logger.error(
            "[rds] describe_db_instances failed for db=%s region=%s: %s",
            db_instance_identifier,
            region,
            result.get("error"),
        )
        return {
            "source": "rds",
            "available": False,
            "db_instance_identifier": db_instance_identifier,
            "error": "Failed to describe the RDS instance. Check server logs for details.",
        }

    instances = (result.get("data") or {}).get("DBInstances") or []
    if not instances:
        return {
            "source": "rds",
            "available": False,
            "db_instance_identifier": db_instance_identifier,
            "error": "No RDS instance found with the given identifier.",
        }

    if len(instances) > 1:
        logger.warning(
            "[rds] describe_db_instances returned %d instances for db=%s; "
            "using the first result only.",
            len(instances),
            db_instance_identifier,
        )

    instance = instances[0]
    endpoint = instance.get("Endpoint") or {}

    return {
        "source": "rds",
        "available": True,
        "db_instance_identifier": db_instance_identifier,
        "status": instance.get("DBInstanceStatus"),
        "engine": instance.get("Engine"),
        "engine_version": instance.get("EngineVersion"),
        "instance_class": instance.get("DBInstanceClass"),
        "multi_az": instance.get("MultiAZ"),
        "publicly_accessible": instance.get("PubliclyAccessible"),
        "storage_type": instance.get("StorageType"),
        "allocated_storage_gb": instance.get("AllocatedStorage"),
        "endpoint": {
            "address": endpoint.get("Address"),
            "port": endpoint.get("Port"),
        },
        "availability_zone": instance.get("AvailabilityZone"),
        "preferred_backup_window": instance.get("PreferredBackupWindow"),
        "backup_retention_period": instance.get("BackupRetentionPeriod"),
        "error": None,
    }
