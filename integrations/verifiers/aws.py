"""AWS integration verifier — STS caller-identity probe."""

from __future__ import annotations

from typing import Any

import boto3

from integrations.config_models import AWSIntegrationConfig
from integrations.verification import register_verifier, result


def _build_sts_client(aws_config: AWSIntegrationConfig) -> tuple[Any, str, str]:
    region = aws_config.region
    role_arn = aws_config.role_arn
    external_id = aws_config.external_id
    if role_arn:
        base_sts_client = boto3.client("sts", region_name=region)
        assume_role_args: dict[str, str] = {
            "RoleArn": role_arn,
            "RoleSessionName": "TracerIntegrationVerify",
        }
        if external_id:
            assume_role_args["ExternalId"] = external_id
        credentials = base_sts_client.assume_role(**assume_role_args)["Credentials"]
        return (
            boto3.client(
                "sts",
                region_name=region,
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            ),
            region,
            "assume-role",
        )

    static_credentials = aws_config.credentials
    if static_credentials is None:
        raise ValueError("Missing AWS role_arn or credentials.")
    return (
        boto3.client(
            "sts",
            region_name=region,
            aws_access_key_id=static_credentials.access_key_id,
            aws_secret_access_key=static_credentials.secret_access_key,
            aws_session_token=static_credentials.session_token or None,
        ),
        region,
        "static-creds",
    )


@register_verifier("aws")
def verify_aws(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        aws_config = AWSIntegrationConfig.model_validate(config)
    except Exception as err:
        return result("aws", source, "missing", str(err))
    try:
        sts_client, region, mode = _build_sts_client(aws_config)
        identity = sts_client.get_caller_identity()
    except Exception as exc:
        return result("aws", source, "failed", f"AWS STS check failed: {exc}")

    account = str(identity.get("Account", "")).strip()
    arn = str(identity.get("Arn", "")).strip()
    return result(
        "aws",
        source,
        "passed",
        (
            f"Connected to AWS STS via {mode} in {region}; "
            f"caller identity account={account or 'unknown'} arn={arn or 'unknown'}."
        ),
    )
