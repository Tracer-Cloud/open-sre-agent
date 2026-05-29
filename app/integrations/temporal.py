"""Temporal workflow platform integration config and verification."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TemporalConfig(BaseModel):
    """Configuration for connecting to a Temporal server."""

    host: str = Field(
        default="localhost",
        description="Temporal server hostname or IP address.",
    )
    port: int = Field(
        default=7233,
        description="Temporal server gRPC port (default 7233).",
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
        TEMPORAL_PORT          — gRPC port   (default: 7233)
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
