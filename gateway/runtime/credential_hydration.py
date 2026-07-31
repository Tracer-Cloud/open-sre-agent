"""Fail-closed startup hydration of tenant integration credentials."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Any, Protocol

from config.constants.tenancy import (
    CREDENTIALS_API_URL_ENV,
    CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV,
    INTEGRATIONS_SECRET_ARN_ENV,
    TENANT_ORGANIZATION_ID_ENV,
)
from integrations.credentials_api import CredentialsApiClient, hydrate_integration_store
from integrations.secrets_vault import hydrate_integration_store_from_secret


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
    integrations_secret_arn: str | None = None

    @classmethod
    def from_environment(cls) -> CredentialHydrationConfig | None:
        """Return ``None`` when disabled, and reject partial configuration."""
        required_values = {
            TENANT_ORGANIZATION_ID_ENV: os.getenv(TENANT_ORGANIZATION_ID_ENV, "").strip(),
            CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV: os.getenv(
                CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, ""
            ).strip(),
        }
        credentials_api_url = os.getenv(CREDENTIALS_API_URL_ENV, "").strip()
        integrations_secret_arn = os.getenv(INTEGRATIONS_SECRET_ARN_ENV, "").strip()
        optional_values = (credentials_api_url, integrations_secret_arn)
        if not any(required_values.values()) and not any(optional_values):
            return None
        missing = [name for name, value in required_values.items() if not value]
        if missing:
            raise ValueError("Credential hydration configuration is incomplete")
        if credentials_api_url and not credentials_api_url.lower().startswith("https://"):
            raise ValueError("Credentials API URL must use HTTPS")
        return cls(
            organization_id=required_values[TENANT_ORGANIZATION_ID_ENV],
            credentials_api_url=credentials_api_url or None,
            bootstrap_secret_arn=required_values[CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV],
            integrations_secret_arn=integrations_secret_arn or None,
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
        bootstrap = _parse_bootstrap_secret(
            self._secret_string(self._config.bootstrap_secret_arn, "Bootstrap")
        )
        # The credentials API wins when both are configured: an operator who
        # sets a URL is deliberately routing this silo away from its secret.
        if self._config.credentials_api_url is not None:
            self._hydrate_from_credentials_api(bootstrap)
            return replace(bootstrap, integrations_hydrated=True)
        if self._config.integrations_secret_arn is not None:
            hydrate_integration_store_from_secret(
                self._secret_string(self._config.integrations_secret_arn, "Integrations")
            )
            return replace(bootstrap, integrations_hydrated=True)
        return bootstrap

    def _secret_string(self, secret_arn: str, label: str) -> str:
        """Read one pinned secret ARN, rejecting a non-string value."""
        response = self._secrets_client.get_secret_value(SecretId=secret_arn)
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise ValueError(f"{label} secret has no string value")
        return secret_string

    def _hydrate_from_credentials_api(self, bootstrap: GatewayBootstrap) -> None:
        """Pull this tenant's store from the webapp over HTTPS."""
        if bootstrap.credentials_api_token is None:
            raise ValueError("Bootstrap secret has no credentials API token")
        if self._config.credentials_api_url is None:
            raise ValueError("Credentials API URL is not configured")
        with CredentialsApiClient(
            base_url=self._config.credentials_api_url,
            bootstrap_credential=bootstrap.credentials_api_token,
        ) as client:
            hydrate_integration_store(
                client=client,
                organization_id=self._config.organization_id,
            )


__all__ = [
    "CredentialHydrationConfig",
    "GatewayBootstrap",
    "GatewayCredentialHydrator",
    "SecretsManagerClient",
]
