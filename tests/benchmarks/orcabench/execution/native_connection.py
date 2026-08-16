"""Connection-only bridge from ORCA evidence sources to OpenSRE."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tests.benchmarks.orcabench.config import GrafanaSettings, ToolCapabilityMode
from tests.benchmarks.orcabench.execution.orca_telemetry import (
    OrcaTelemetryBackend,
    OrcaTelemetryWindowPolicy,
)


class OrcaNativeConnections:
    """Build native OpenSRE telemetry and source connections without evidence."""

    def __init__(
        self,
        settings: GrafanaSettings,
        source_root: Path,
        *,
        tool_capability_mode: ToolCapabilityMode = "native",
    ) -> None:
        self._settings = settings
        self._source_root = source_root
        self._tool_capability_mode = tool_capability_mode

    def build(
        self,
        environ: dict[str, str],
        incident_window: dict[str, Any],
    ) -> dict[str, Any]:
        """Return pre-resolved read-only connections expected by OpenSRE."""
        endpoint = environ.get("GRAFANA_URL", "").strip().rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("GRAFANA_URL must be an absolute HTTP(S) URL")
        start_time = str(incident_window["since"])
        end_time = str(incident_window["until"])
        if self._tool_capability_mode == "terminus_parity":
            window_policy = OrcaTelemetryWindowPolicy.terminus_parity(
                start_time=start_time,
                end_time=end_time,
            )
        else:
            window_policy = OrcaTelemetryWindowPolicy.native(
                start_time=start_time,
                end_time=end_time,
            )

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
                "_backend": OrcaTelemetryBackend(
                    endpoint=endpoint,
                    username=self._settings.username,
                    password=self._settings.password,
                    verify_ssl=self._settings.verify_ssl,
                    start_time=start_time,
                    end_time=end_time,
                    window_policy=window_policy,
                ),
            },
            "local_source": {
                "root_path": str(self._source_root),
                "connection_verified": self._source_root.is_dir(),
            },
        }
