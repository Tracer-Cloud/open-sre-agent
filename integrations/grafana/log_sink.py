"""Grafana log sink for investigation events.

Converts investigation state into:
1. Loki push streams (via direct Loki endpoint, not datasource proxy)
2. Grafana annotations (via the existing Grafana client)

Env vars:
- GRAFANA_LOKI_PUSH_URL: Loki push endpoint (a *_URL value; read plain, never
  keyring-backed, per docs/adding-tools-and-integrations.md#credential-resolution).
- GRAFANA_WRITE_TOKEN: Loki write auth token (separate from Grafana read token).
  A *_TOKEN secret, resolved env-then-keyring via ``resolve_env_credential``.
  If unset, falls back to the Grafana read_token for annotation creation.
"""

from __future__ import annotations

import json
import logging
import os
import time as time_mod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import requests
from requests import RequestException

from config.llm_credentials import resolve_env_credential
from integrations.grafana.base import GrafanaClientBase

logger = logging.getLogger(__name__)

_DEFAULT_LOKI_LABELS: Final[dict[str, str]] = {
    "job": "opensre",
    "source": "investigation",
}
_DEFAULT_ANNOTATION_TAGS: Final[list[str]] = ["opensre", "investigation"]

_SEVERITY_EMOJI: Final[dict[str, str]] = {
    "critical": "🔴",
    "high": "🟠",
    "warning": "🟡",
    "low": "🟢",
}


@dataclass(frozen=True, slots=True)
class GrafanaLogSinkConfig:
    """Knobs for the Grafana log sink. Frozen for test safety."""

    push_to_loki: bool = True
    create_annotations: bool = True
    loki_labels: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_LOKI_LABELS))
    annotation_tags: list[str] = field(default_factory=lambda: list(_DEFAULT_ANNOTATION_TAGS))
    max_evidence_entries: int = 10
    max_summary_chars: int = 2000
    loki_push_url: str = ""  # reads from GRAFANA_LOKI_PUSH_URL if empty
    loki_write_token: str = ""  # reads from GRAFANA_WRITE_TOKEN if empty


class GrafanaLogSink:
    """Format and forward investigation events to Grafana."""

    __slots__ = ("_client", "_config", "_loki_push_url", "_loki_write_token")

    def __init__(
        self,
        client: GrafanaClientBase | None = None,
        *,
        config: GrafanaLogSinkConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or GrafanaLogSinkConfig()
        # Resolve Loki push config: explicit > env > empty (disabled)
        self._loki_push_url = (
            self._config.loki_push_url or os.getenv("GRAFANA_LOKI_PUSH_URL", "").strip()
        )
        self._loki_write_token = (
            self._config.loki_write_token or resolve_env_credential("GRAFANA_WRITE_TOKEN").strip()
        )

    def send_investigation_report(
        self,
        state: Mapping[str, Any],
        *,
        messages: Mapping[str, Any],
    ) -> bool:
        """Push report to Grafana. Returns True if ≥1 channel succeeded."""
        loki_ok = False
        annotation_ok = False
        if self._config.push_to_loki:
            loki_ok = self._push_to_loki(state, messages)
        if self._config.create_annotations:
            annotation_ok = self._create_annotation(state, messages)
        return loki_ok or annotation_ok

    def _push_to_loki(self, state: Mapping[str, Any], messages: Mapping[str, Any]) -> bool:
        """Build Loki stream and POST to the Loki push endpoint."""
        if not self._loki_push_url:
            logger.debug("[grafana-sink] Loki push skipped: GRAFANA_LOKI_PUSH_URL not set")
            return False
        streams = self._build_loki_streams(state, messages)
        try:
            url = self._loki_push_url.rstrip("/")
            if not url.endswith("/loki/api/v1/push"):
                url = f"{url}/loki/api/v1/push"
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._loki_write_token:
                headers["Authorization"] = f"Bearer {self._loki_write_token}"
            # Loki-only mode (no client) has no account-level TLS config to draw
            # on, so default to verifying certs. When a Grafana client *is*
            # configured, honor its ssl_verify (e.g. self-signed on-prem certs).
            verify = getattr(self._client, "ssl_verify", True)
            resp = requests.post(
                url, json={"streams": streams}, headers=headers, timeout=10, verify=verify
            )
            if resp.status_code in (200, 204):
                return True
            resp.raise_for_status()
            return True
        except RequestException:
            logger.warning("[grafana-sink] Loki push failed", exc_info=True)
            return False

    def _build_loki_streams(
        self,
        state: Mapping[str, Any],
        messages: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Convert investigation state to Loki stream format."""
        severity = str(state.get("severity", "unknown")).lower()
        alert_source = str(state.get("alert_source", "unknown")).lower()
        # Only low-cardinality labels; everything else in JSON log line
        labels = {**self._config.loki_labels, "severity": severity, "alert_source": alert_source}

        evidence = state.get("evidence", [])
        evidence = (
            evidence[: self._config.max_evidence_entries] if isinstance(evidence, list) else []
        )

        log_line = json.dumps(
            {
                "alert_name": state.get("alert_name", ""),
                "pipeline_name": state.get("pipeline_name", ""),
                "severity": severity,
                "root_cause": state.get("root_cause", ""),
                "remediation_steps": state.get("remediation_steps", []),
                "evidence_count": len(evidence),
                "correlation": state.get("correlation", {}),
                "report_summary": str(messages.get("slack_text", ""))[:500],
                "run_id": state.get("run_id", ""),
            },
            default=str,
        )

        timestamp_ns = str(int(time_mod.time() * 1e9))
        return [{"stream": labels, "values": [[timestamp_ns, log_line]]}]

    def _create_annotation(self, state: Mapping[str, Any], messages: Mapping[str, Any]) -> bool:
        """Create a Grafana annotation summarizing the investigation."""
        if self._client is None:
            logger.debug("[grafana-sink] Annotation skipped: no Grafana client configured")
            return False
        try:
            severity = str(state.get("severity", "unknown")).lower()
            emoji = _SEVERITY_EMOJI.get(severity, "⚠️")
            alert_name = state.get("alert_name", "Unknown alert")
            root_cause = state.get("root_cause", "")

            text_parts = [
                f"{emoji} **OpenSRE Investigation**: {alert_name}",
                f"**Severity**: {severity.upper()}",
            ]
            if root_cause:
                text_parts.append(f"**Root cause**: {root_cause}")
            report = str(messages.get("slack_text", ""))
            if report:
                text_parts.append(f"**Summary**:\n{report[: self._config.max_summary_chars]}")

            tags = list(self._config.annotation_tags) + [severity]
            alert_source = state.get("alert_source")
            if alert_source:
                tags.append(str(alert_source).lower())

            result = self._client.create_annotation(text="\n".join(text_parts), tags=tags)
            return bool(result.get("success"))
        except RequestException:
            logger.warning("[grafana-sink] Annotation creation failed", exc_info=True)
            return False
