"""Configurator handler for the AWS integration."""

from __future__ import annotations

from integrations.store import upsert_integration
from platform.terminal.theme import SECONDARY
from surfaces.cli.wizard._ui import (
    Choice,
    _choose,
    _console,
    _integration_defaults,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.env_sync import sync_env_values
from surfaces.cli.wizard.integration_health import validate_aws_integration


def _configure_aws() -> tuple[str, str]:
    existing, credentials = _integration_defaults("aws")
    default_auth_mode = "role" if _string_value(existing.get("role_arn")) else "keys"
    auth_mode = _choose(
        "Choose the AWS authentication method:",
        [
            Choice(value="role", label="IAM role ARN"),
            Choice(value="keys", label="Access key + secret"),
        ],
        default=default_auth_mode,
    )

    while True:
        region = _prompt_value(
            "AWS region",
            default=_string_value(credentials.get("region"), "us-east-1"),
        )
        if auth_mode == "role":
            role_arn = _prompt_value(
                "IAM role ARN",
                default=_string_value(existing.get("role_arn")),
            )
            external_id = _prompt_value(
                "External ID",
                default=_string_value(existing.get("external_id")),
                allow_empty=True,
            )
            with _console.status("Validating AWS role...", spinner="dots"):
                result = validate_aws_integration(
                    region=region,
                    role_arn=role_arn,
                    external_id=external_id,
                )
            _render_integration_result("AWS", result)
            if result.ok:
                upsert_integration(
                    "aws",
                    {
                        "role_arn": role_arn,
                        "external_id": external_id,
                        "credentials": {"region": region},
                    },
                )
                env_path = sync_env_values({"AWS_REGION": region})
                return "AWS", str(env_path)
        else:
            access_key_id = _prompt_value(
                "AWS access key ID",
                default=_string_value(credentials.get("access_key_id")),
                secret=True,
            )
            secret_access_key = _prompt_value(
                "AWS secret access key",
                default=_string_value(credentials.get("secret_access_key")),
                secret=True,
            )
            session_token = _prompt_value(
                "AWS session token",
                default=_string_value(credentials.get("session_token")),
                secret=True,
                allow_empty=True,
            )
            with _console.status("Validating AWS credentials...", spinner="dots"):
                result = validate_aws_integration(
                    region=region,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    session_token=session_token,
                )
            _render_integration_result("AWS", result)
            if result.ok:
                upsert_integration(
                    "aws",
                    {
                        "credentials": {
                            "access_key_id": access_key_id,
                            "secret_access_key": secret_access_key,
                            "session_token": session_token,
                            "region": region,
                        }
                    },
                )
                env_path = sync_env_values(
                    {
                        "AWS_REGION": region,
                    }
                )
                return "AWS", str(env_path)

        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
