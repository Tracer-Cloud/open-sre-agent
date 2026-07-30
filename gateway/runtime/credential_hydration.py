"""Fail-closed startup hydration of tenant integration credentials."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Any, Protocol

from integrations.credentials_api import CredentialsApiClient, hydrate_integration_store

_ORGANIZATION_ID = "ORGANIZATION_ID"
_API_URL = "OPENSRE_CREDENTIALS_API_URL"
_SECRET_ARN = "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN"


class SecretsManagerClient(Protocol):
    """Narrow boto3 Secrets Manager surface used at Gateway startup."""

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        """Return exactly the configured bootstrap secret."""


@dataclass(frozen=True, slots=True)
class GatewayBootstrap:
    """Decrypted bootstrap values held in memory for this process only."""

    credentials_api_token: str | None = None
    database_url: str | None = None
    integrations_hydrated: bool = False


@dataclass(frozen=True, slots=True)
class CredentialHydrationConfig:
    """Non-secret references required to hydrate one tenant."""

    organization_id: str
    bootstrap_secret_arn: str
    credentials_api_url: str | None = None

    @classmethod
    def from_environment(cls) -> CredentialHydrationConfig | None:
        """Return ``None`` when disabled, and reject partial configuration."""
        required_values = {
            _ORGANIZATION_ID: os.getenv(_ORGANIZATION_ID, "").strip(),
            _SECRET_ARN: os.getenv(_SECRET_ARN, "").strip(),
        }
        credentials_api_url = os.getenv(_API_URL, "").strip()
        if not any(required_values.values()) and not credentials_api_url:
            return None
        missing = [name for name, value in required_values.items() if not value]
        if missing:
            raise ValueError("Credential hydration configuration is incomplete")
        if credentials_api_url and not credentials_api_url.lower().startswith("https://"):
            raise ValueError("Credentials API URL must use HTTPS")
        return cls(
            organization_id=required_values[_ORGANIZATION_ID],
            credentials_api_url=credentials_api_url or None,
            bootstrap_secret_arn=required_values[_SECRET_ARN],
        )


def _parse_bootstrap_secret(secret_string: str) -> GatewayBootstrap:
    """Accept a legacy raw token or a secret-safe JSON bootstrap bundle."""
    if not secret_string:
        raise ValueError("Bootstrap secret is empty")
    try:
        value = json.loads(secret_string)
    except json.JSONDecodeError:
        return GatewayBootstrap(credentials_api_token=secret_string)
    if not isinstance(value, dict):
        raise ValueError("Bootstrap secret has an invalid shape")
    token = value.get("credentials_api_token")
    database_url = value.get("database_url")
    if token is not None and (not isinstance(token, str) or not token):
        raise ValueError("Bootstrap secret has an invalid shape")
    if database_url is not None and (not isinstance(database_url, str) or not database_url):
        raise ValueError("Bootstrap secret has an invalid shape")
    if token is None and database_url is None:
        raise ValueError("Bootstrap secret has an invalid shape")
    return GatewayBootstrap(credentials_api_token=token, database_url=database_url)


class GatewayCredentialHydrator:
    """Fetch one allowed secret, then materialize the validated local v2 store."""

    def __init__(
        self,
        *,
        config: CredentialHydrationConfig,
        secrets_client: SecretsManagerClient,
    ) -> None:
        self._config = config
        self._secrets_client = secrets_client

    @classmethod
    def from_environment(cls) -> GatewayCredentialHydrator | None:
        """Compose the production hydrator from task-role AWS credentials."""
        config = CredentialHydrationConfig.from_environment()
        if config is None:
            return None
        import boto3

        return cls(config=config, secrets_client=boto3.client("secretsmanager"))

    def hydrate(self) -> GatewayBootstrap:
        """Hydrate credentials atomically before any runtime component starts."""
        response = self._secrets_client.get_secret_value(SecretId=self._config.bootstrap_secret_arn)
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise ValueError("Bootstrap secret has no string value")
        bootstrap = _parse_bootstrap_secret(secret_string)
        if self._config.credentials_api_url is None:
            return bootstrap
        if bootstrap.credentials_api_token is None:
            raise ValueError("Bootstrap secret has no credentials API token")
        with CredentialsApiClient(
            base_url=self._config.credentials_api_url,
            bootstrap_credential=bootstrap.credentials_api_token,
        ) as client:
            hydrate_integration_store(
                client=client,
                organization_id=self._config.organization_id,
            )
        return replace(bootstrap, integrations_hydrated=True)


__all__ = [
    "CredentialHydrationConfig",
    "GatewayBootstrap",
    "GatewayCredentialHydrator",
    "SecretsManagerClient",
]
