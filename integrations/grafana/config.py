"""Grafana account configuration."""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import field_validator

from config.strict_config import StrictConfigModel


class GrafanaAccountConfig(StrictConfigModel):
    """Configuration for a Grafana Cloud account."""

    account_id: str
    instance_url: str
    read_token: str
    loki_datasource_uid: str = ""
    tempo_datasource_uid: str = ""
    mimir_datasource_uid: str = ""
    description: str = ""
    username: str = ""
    password: str = ""
    verify_ssl: bool = True
    ca_bundle: str = ""

    @field_validator("instance_url", mode="before")
    @classmethod
    def _normalize_instance_url(cls, value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @property
    def ssl_verify(self) -> bool | str:
        """Value to pass as ``requests``' ``verify=`` kwarg."""
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_ssl

    @property
    def uses_local_anonymous_auth(self) -> bool:
        """Allow localhost Grafana to work without a bearer token."""
        host = urlparse(self.instance_url).hostname or ""
        return bool(
            self.instance_url
            and not self.read_token
            and host in {"localhost", "127.0.0.1", "0.0.0.0"}
        )

    @property
    def is_configured(self) -> bool:
        """Check if account has valid configuration."""
        return bool(self.instance_url and (self.read_token or self.uses_local_anonymous_auth))
