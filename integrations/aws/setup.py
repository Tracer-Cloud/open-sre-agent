"""What AWS needs before it is considered configured.

Auth is a picker (IAM role ARN *or* access key + secret). Region is always
asked; the picker only scopes the auth fields. The either/or rule also lives
on ``AWSIntegrationConfig`` (the model
:func:`integrations.aws.verifier.verify_aws` validates against), so setup and
health checks agree for any surface that skips the picker.

Static keys are collected flat so each field can mirror an env var; the
verifier wrapper nests them under ``credentials`` for the config model.
``classify()`` already builds that nested shape from flat store records.
"""

from __future__ import annotations

from typing import Any

from config.constants.aws import (
    AWS_ACCESS_KEY_ID_ENV,
    AWS_EXTERNAL_ID_ENV,
    AWS_REGION_ENV,
    AWS_ROLE_ARN_ENV,
    AWS_SECRET_ACCESS_KEY_ENV,
    AWS_SESSION_TOKEN_ENV,
)
from integrations.aws.verifier import verify_aws
from integrations.setup_flow import IntegrationSetupSpec, SetupField, SetupMode

REGION_FIELD = "region"
ROLE_ARN_FIELD = "role_arn"
EXTERNAL_ID_FIELD = "external_id"
ACCESS_KEY_ID_FIELD = "access_key_id"
SECRET_ACCESS_KEY_FIELD = "secret_access_key"
SESSION_TOKEN_FIELD = "session_token"


def shape_aws_credentials(config: dict[str, Any]) -> dict[str, Any]:
    """Return *config* shaped for ``AWSIntegrationConfig`` when keys are flat."""
    access_key_id = str(config.get(ACCESS_KEY_ID_FIELD) or "").strip()
    secret_access_key = str(config.get(SECRET_ACCESS_KEY_FIELD) or "").strip()
    if not (access_key_id and secret_access_key):
        return config
    return {
        "region": str(config.get(REGION_FIELD) or "").strip() or "us-east-1",
        "role_arn": str(config.get(ROLE_ARN_FIELD) or "").strip(),
        "external_id": str(config.get(EXTERNAL_ID_FIELD) or "").strip(),
        "credentials": {
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
            "session_token": str(config.get(SESSION_TOKEN_FIELD) or "").strip(),
        },
    }


def _verify_aws_setup(source: str, config: dict[str, Any]) -> dict[str, str]:
    return verify_aws(source, shape_aws_credentials(config))


AWS_SETUP = IntegrationSetupSpec(
    service="aws",
    fields=(
        SetupField(
            name=REGION_FIELD,
            label="AWS region",
            prompt="Region",
            env_var=AWS_REGION_ENV,
            default="us-east-1",
        ),
        SetupField(
            name=ROLE_ARN_FIELD,
            label="IAM Role ARN",
            env_var=AWS_ROLE_ARN_ENV,
            required=False,
        ),
        SetupField(
            name=EXTERNAL_ID_FIELD,
            label="External ID",
            prompt="External ID (optional)",
            env_var=AWS_EXTERNAL_ID_ENV,
            required=False,
        ),
        SetupField(
            name=ACCESS_KEY_ID_FIELD,
            label="AWS Access Key ID",
            prompt="AWS_ACCESS_KEY_ID",
            env_var=AWS_ACCESS_KEY_ID_ENV,
            required=False,
        ),
        SetupField(
            name=SECRET_ACCESS_KEY_FIELD,
            label="AWS Secret Access Key",
            prompt="AWS_SECRET_ACCESS_KEY",
            env_var=AWS_SECRET_ACCESS_KEY_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=SESSION_TOKEN_FIELD,
            label="AWS Session Token",
            prompt="Session token (optional)",
            env_var=AWS_SESSION_TOKEN_ENV,
            secret=True,
            required=False,
        ),
    ),
    mode_prompt="AWS authentication method:",
    modes=(
        SetupMode(
            value="role",
            label="IAM Role ARN",
            fields=(ROLE_ARN_FIELD, EXTERNAL_ID_FIELD),
        ),
        SetupMode(
            value="keys",
            label="Access Key + Secret",
            fields=(ACCESS_KEY_ID_FIELD, SECRET_ACCESS_KEY_FIELD, SESSION_TOKEN_FIELD),
        ),
    ),
    verify=_verify_aws_setup,
)

__all__ = [
    "ACCESS_KEY_ID_FIELD",
    "AWS_SETUP",
    "EXTERNAL_ID_FIELD",
    "REGION_FIELD",
    "ROLE_ARN_FIELD",
    "SECRET_ACCESS_KEY_FIELD",
    "SESSION_TOKEN_FIELD",
    "shape_aws_credentials",
]
