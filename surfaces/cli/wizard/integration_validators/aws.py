"""Client-backed validator for the AWS integration."""

from __future__ import annotations

from integrations.config_models import AWSIntegrationConfig

from .shared import IntegrationHealthResult


def validate_aws_integration(
    *,
    region: str,
    role_arn: str = "",
    external_id: str = "",
    access_key_id: str = "",
    secret_access_key: str = "",
    session_token: str = "",
) -> IntegrationHealthResult:
    """Validate AWS credentials with STS GetCallerIdentity."""
    try:
        import boto3
    except ImportError:
        return IntegrationHealthResult(
            ok=False, detail="AWS validation failed: boto3 is not installed."
        )

    try:
        aws_config = AWSIntegrationConfig.model_validate(
            {
                "region": region,
                "role_arn": role_arn,
                "external_id": external_id,
                "credentials": (
                    {
                        "access_key_id": access_key_id,
                        "secret_access_key": secret_access_key,
                        "session_token": session_token,
                    }
                    if access_key_id or secret_access_key or session_token
                    else None
                ),
            }
        )
        if role_arn:
            sts = boto3.client("sts", region_name=aws_config.region)
            assume_kwargs: dict[str, str] = {
                "RoleArn": aws_config.role_arn,
                "RoleSessionName": "opensre-onboard-check",
            }
            if aws_config.external_id:
                assume_kwargs["ExternalId"] = aws_config.external_id
            creds = sts.assume_role(**assume_kwargs)["Credentials"]
            assumed = boto3.client(
                "sts",
                region_name=aws_config.region,
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
            identity = assumed.get_caller_identity()
            return IntegrationHealthResult(
                ok=True,
                detail=f"AWS role validated for account {identity.get('Account')} as {identity.get('Arn')}.",
            )

        sts = boto3.client(
            "sts",
            region_name=aws_config.region,
            aws_access_key_id=aws_config.credentials.access_key_id
            if aws_config.credentials
            else "",
            aws_secret_access_key=aws_config.credentials.secret_access_key
            if aws_config.credentials
            else "",
            aws_session_token=(
                aws_config.credentials.session_token if aws_config.credentials else ""
            )
            or None,
        )
        identity = sts.get_caller_identity()
        return IntegrationHealthResult(
            ok=True,
            detail=f"AWS credentials validated for account {identity.get('Account')} as {identity.get('Arn')}.",
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"AWS validation failed: {err}")
