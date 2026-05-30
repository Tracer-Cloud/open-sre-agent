"""Temporal workflow platform integration config and verification.

This integration uses Temporal's HTTP API gateway (available on the same
port as gRPC, default 7233). For self-hosted Temporal, no extra configuration
is needed. For Temporal Cloud, set TEMPORAL_TLS=true and TEMPORAL_API_KEY.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.integrations.config_models import TemporalIntegrationConfig
from pydantic import BaseModel, Field

"""Temporal workflow platform integration config and verification.

This integration uses Temporal's HTTP API gateway (available on the same
port as gRPC, default 7233). For self-hosted Temporal, no extra configuration
is needed. For Temporal Cloud, set TEMPORAL_TLS=true and TEMPORAL_API_KEY.
"""

class TemporalConfig(BaseModel):
    """Configuration for connecting to a Temporal server."""

    host: str = Field(
        default="localhost",
        description="Temporal server hostname or IP address.",
    )
    port: int = Field(
    default=7233,
    description="Temporal server HTTP API port (default 7233). "
                "For self-hosted Temporal this is the same port as gRPC. "
                "Temporal Cloud uses port 7233 for both gRPC and the HTTP API gateway.",
    )
    namespace: str = Field(
        default="default",
        description="Temporal namespace to query.",
    )
    api_key: str | None = Field(
        default=None,
        description="API key for Temporal Cloud authentication (optional for self-hosted).",
    )
    tls: bool = Field(
        default=False,
        description="Whether to use TLS for the connection (required for Temporal Cloud).",
    )

    @property
    def base_url(self) -> str:
        scheme = "https" if self.tls else "http"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def connection_verified(self) -> bool:
        """Lightweight check — host and port are set."""
        return bool(self.host and self.port)


def load_temporal_config_from_env() -> TemporalConfig:
    """Load Temporal config from environment variables.

    Expected env vars:
        TEMPORAL_HOST          — server host (default: localhost)
        TEMPORAL_PORT          — HTTP API port (default: 7233)
        TEMPORAL_NAMESPACE     — namespace   (default: default)
        TEMPORAL_API_KEY       — API key for Temporal Cloud (optional)
        TEMPORAL_TLS           — "true" to enable TLS (default: false)
    """
    import os

    return TemporalConfig(
        host=os.environ.get("TEMPORAL_HOST", "localhost"),
        port=int(os.environ.get("TEMPORAL_PORT", "7233")),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        api_key=os.environ.get("TEMPORAL_API_KEY"),
        tls=os.environ.get("TEMPORAL_TLS", "false").lower() == "true",
    )

def load_temporal_config_from_integration(integration_config: TemporalIntegrationConfig) -> TemporalConfig:
    """Wire TemporalIntegrationConfig (from registry) into TemporalConfig (used by client)."""
    return TemporalConfig(
        host=integration_config.host,
        port=integration_config.port,
        namespace=integration_config.namespace,
        api_key=integration_config.api_key or None,
        tls=integration_config.tls,
    )
