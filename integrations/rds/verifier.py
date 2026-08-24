"""RDS integration verifier — describes the configured DB instance.

Hand-written rather than :func:`integrations.verification.register_probe_verifier`
because RDS has no dedicated client class to probe: it reads through the shared,
safety-validated ``execute_aws_sdk_call`` the same way the ``describe_rds_instance``
tool does, mirroring ``integrations/aws/verifier.py``'s pattern for the same reason.
"""

from __future__ import annotations

from typing import Any

from integrations.aws.aws_sdk_client import execute_aws_sdk_call
from integrations.rds import RDSConfig
from integrations.verification import register_verifier, result


@register_verifier("rds")
def verify_rds(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        rds_config = RDSConfig.model_validate(config)
    except Exception as err:
        return result("rds", source, "missing", str(err))
    if not rds_config.is_configured:
        return result(
            "rds",
            source,
            "missing",
            "Missing db_instance_identifier or region.",
        )

    probe = execute_aws_sdk_call(
        service_name="rds",
        operation_name="describe_db_instances",
        parameters={"DBInstanceIdentifier": rds_config.db_instance_identifier},
        region=rds_config.region,
    )
    if not probe.get("success"):
        return result(
            "rds",
            source,
            "failed",
            str(probe.get("error") or "RDS describe_db_instances failed."),
        )

    instances = (probe.get("data") or {}).get("DBInstances") or []
    if not instances:
        return result(
            "rds",
            source,
            "failed",
            f"No RDS instance found with identifier "
            f"{rds_config.db_instance_identifier!r} in {rds_config.region}.",
        )

    instance = instances[0]
    status = instance.get("DBInstanceStatus", "unknown")
    engine = instance.get("Engine", "unknown")
    return result(
        "rds",
        source,
        "passed",
        f"Connected to RDS in {rds_config.region}; instance "
        f"{rds_config.db_instance_identifier} engine={engine} status={status}.",
    )
