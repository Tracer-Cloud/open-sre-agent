"""Connection-only bridge from the ORCA Grafana contract to OpenSRE."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from tests.benchmarks.orcabench.config import GrafanaSettings


class OrcaGrafanaConnection:
    """Build the native OpenSRE Grafana source without diagnostic information."""

    def __init__(self, settings: GrafanaSettings) -> None:
        self._settings = settings

    def build(self, environ: dict[str, str]) -> dict[str, Any]:
        """Return the single pre-resolved Grafana connection expected by OpenSRE."""
        endpoint = environ.get("GRAFANA_URL", "").strip().rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("GRAFANA_URL must be an absolute HTTP(S) URL")

        # OpenSRE supports basic auth for requests, but its current
        # GrafanaAccountConfig.is_configured predicate also requires a nonempty
        # read token for non-loopback hostnames. This inert value satisfies that
        # predicate; request authentication still prefers username/password.
        return {
            "grafana": {
                "endpoint": endpoint,
                "api_key": self._settings.compatibility_token,
                "username": self._settings.username,
                "password": self._settings.password,
                "verify_ssl": self._settings.verify_ssl,
                "connection_verified": True,
            }
        }
