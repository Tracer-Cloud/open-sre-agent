"""CloudFormation export names shared by Fargate infrastructure stacks."""

from __future__ import annotations

# Fleet exports the control plane imports via Fn::ImportValue. Only stable
# infrastructure identity belongs here: CloudFormation refuses to update an
# export while any stack imports it, so a per-release value (e.g. the gateway
# image digest) in this tuple would permanently block fleet updates. Volatile
# values are passed to the control-plane stack as its own parameters instead.
CONTROL_PLANE_FLEET_ENVIRONMENT = (
    "OPENSRE_FARGATE_CLUSTER_ARN",
    "OPENSRE_ECS_EXECUTION_ROLE_ARN",
    "OPENSRE_GATEWAY_LOG_GROUP",
    "OPENSRE_FARGATE_SUBNET_IDS",
    "OPENSRE_FARGATE_SECURITY_GROUP_IDS",
    "OPENSRE_S3_FILES_MOUNT_SECURITY_GROUP_IDS",
    "OPENSRE_S3_FILESYSTEM_ID",
    "OPENSRE_S3_FILESYSTEM_ARN",
    "OPENSRE_CREDENTIALS_API_URL",
    "OPENSRE_FARGATE_RESOURCE_PREFIX",
)

PUBLIC_FORWARDER_FLEET_ENVIRONMENT = ("OPENSRE_FARGATE_RESOURCE_PREFIX",)

# CloudFormation rejects empty export values, but optional fleet parameters
# (e.g. CredentialsApiUrl before the API stacks exist) must still be exported
# because consumers import every fleet export unconditionally. The fleet stack
# exports this sentinel instead of an empty string; readers normalize it back
# to "" via normalize_fleet_export().
FLEET_EXPORT_UNSET = "-"


def normalize_fleet_export(value: str) -> str:
    """Map the non-empty export sentinel back to an empty string."""
    stripped = value.strip()
    return "" if stripped == FLEET_EXPORT_UNSET else stripped


def fleet_export_name(env_name: str) -> str:
    """Build the export name for a control-plane fleet environment variable."""
    return f"opensre-fleet:{env_name.replace('_', '-')}"
