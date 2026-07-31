"""Load one tenant's integration credentials, once, before the gateway serves.

A silo always reads its **bootstrap secret** from Secrets Manager first. That
secret carries the tenant's Neon connection string and, for one of the routes
below, an API token. It never carries integrations.

Integrations then arrive by exactly one of two routes:

* **integrations secret** — read the tenant's IntegrationStore v2 blob from
  Secrets Manager. This is the route deployed silos run on, and it wins when
  both are configured.
* **credentials API** — fetch the store from the webapp over HTTPS. The staged
  fallback, used only when no integrations secret is configured.

Configuring neither leaves the store as shipped, which is how a laptop runs.

Two failure modes are deliberately different. Nothing configured means the
feature is off, so :meth:`CredentialHydrationConfig.from_environment` returns
``None``. Something-but-not-everything configured is a broken deployment, so it
raises rather than starting a silo that would serve the wrong tenant or no
tenant at all.
"""

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

    #: Authorizes the credentials-API route. Unused on the secret route.
    credentials_api_token: str | None = None
    #: Neon connection string; the remote-run worker needs it to poll.
    database_url: str | None = None
    #: Whether a route ran and replaced the local store, as opposed to the
    #: store shipping with the image. Drives one status word.
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
        identity = {
            TENANT_ORGANIZATION_ID_ENV: os.getenv(TENANT_ORGANIZATION_ID_ENV, "").strip(),
            CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV: os.getenv(
                CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, ""
            ).strip(),
        }
        credentials_api_url = os.getenv(CREDENTIALS_API_URL_ENV, "").strip()
        integrations_secret_arn = os.getenv(INTEGRATIONS_SECRET_ARN_ENV, "").strip()

        # Not one variable set anywhere: the feature is off, not misconfigured.
        if not any(identity.values()) and not (credentials_api_url or integrations_secret_arn):
            return None
        # Anything set means a silo meant to hydrate, so identity must be complete.
        missing = [name for name, value in identity.items() if not value]
        if missing:
            raise ValueError("Credential hydration configuration is incomplete")
        if credentials_api_url and not credentials_api_url.lower().startswith("https://"):
            raise ValueError("Credentials API URL must use HTTPS")
        return cls(
            organization_id=identity[TENANT_ORGANIZATION_ID_ENV],
            credentials_api_url=credentials_api_url or None,
            bootstrap_secret_arn=identity[CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV],
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
        """Read the bootstrap secret, then load integrations by one route."""
        bootstrap = _parse_bootstrap_secret(
            self._read_secret(self._config.bootstrap_secret_arn, secret_name="Bootstrap")
        )
        # The tenant's secret wins when both are configured: it is the route the
        # webapp maintains through the control plane, and the one deployed silos
        # run on. The credentials API stays as the staged fallback.
        if self._config.integrations_secret_arn is not None:
            self._load_from_integrations_secret()
        elif self._config.credentials_api_url is not None:
            self._load_from_credentials_api(bootstrap)
        else:
            return bootstrap
        return replace(bootstrap, integrations_hydrated=True)

    def _read_secret(self, secret_arn: str, *, secret_name: str) -> str:
        """Read one pinned ARN. ``secret_name`` only names it in the error."""
        response = self._secrets_client.get_secret_value(SecretId=secret_arn)
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise ValueError(f"{secret_name} secret has no string value")
        return secret_string

    def _load_from_integrations_secret(self) -> None:
        """Replace the local store from this tenant's Secrets Manager blob."""
        if self._config.integrations_secret_arn is None:
            raise ValueError("Integrations secret ARN is not configured")
        hydrate_integration_store_from_secret(
            self._read_secret(self._config.integrations_secret_arn, secret_name="Integrations")
        )

    def _load_from_credentials_api(self, bootstrap: GatewayBootstrap) -> None:
        """Replace the local store from the webapp over HTTPS."""
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
