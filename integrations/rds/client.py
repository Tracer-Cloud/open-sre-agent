"""RDS Describe API client used by verification."""

from __future__ import annotations

from typing import Any

from integrations.aws.aws_sdk_client import execute_aws_sdk_call
from integrations.probes import ProbeResult
from integrations.rds import RDSConfig


class RDSClient:
    """Read-only RDS client. Verification probes ``DescribeDBInstances``."""

    def __init__(self, config: RDSConfig) -> None:
        self.config = config

    def probe_access(self) -> ProbeResult:
        """Confirm the configured instance is reachable with current AWS credentials."""
        if not self.config.is_configured:
            return ProbeResult.missing("Missing RDS DB instance identifier.")

        identifier = self.config.db_instance_identifier
        region = self.config.region
        result = execute_aws_sdk_call(
            service_name="rds",
            operation_name="describe_db_instances",
            parameters={"DBInstanceIdentifier": identifier},
            region=region,
        )
        if not result.get("success"):
            error = str(result.get("error") or "unknown error")
            return ProbeResult.failed(f"RDS DescribeDBInstances failed: {error}")

        instances = _db_instances(result.get("data"))
        if not instances:
            return ProbeResult.failed(f"No RDS instance named {identifier} in {region}.")

        instance = instances[0]
        status = str(instance.get("DBInstanceStatus") or "unknown")
        engine = str(instance.get("Engine") or "unknown")
        return ProbeResult.passed(
            f"Reached RDS instance {identifier} in {region}; status={status} engine={engine}."
        )


def _db_instances(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("DBInstances") or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]
