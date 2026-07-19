"""Grafana ``ReportDeliveryAdapter`` implementation.

Registers into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.grafana`` directly (T-4 layering audit).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel

from config.llm_credentials import resolve_env_credential
from platform.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _GrafanaReportDeliveryAdapter:
    """Grafana delivery adapter — pushes investigation logs/annotations."""

    name = "grafana"

    def _resolve_sink_inputs(
        self,
        resolved: dict[str, Any],
    ) -> tuple[Any | None, Any | None]:
        """
        Resolve Grafana client and log sink config for investigation delivery.

        Returns (client, config) or (None, None) when nothing is deliverable.
        Deliverable when ANY of:
          - GRAFANA_LOKI_PUSH_URL is set (Loki-only mode)
          - Grafana integration has a usable endpoint (annotation mode)
        """
        from integrations.grafana.log_sink import GrafanaLogSinkConfig

        # GRAFANA_LOKI_PUSH_URL is a *_URL value (never keyring-backed); read
        # it plain. GRAFANA_WRITE_TOKEN is a *_TOKEN secret and must resolve
        # env-then-keyring per docs/adding-tools-and-integrations.md#credential-resolution.
        loki_push_url = os.getenv("GRAFANA_LOKI_PUSH_URL", "").strip()
        loki_write_token = resolve_env_credential("GRAFANA_WRITE_TOKEN").strip()

        grafana = resolved.get("grafana") or resolved.get("grafana_local")
        if not grafana:
            logger.debug("[resolve-sink] no grafana integration configured")
            # Check if Loki-only mode is available
            if loki_push_url:
                logger.debug("[resolve-sink] Loki-only mode: GRAFANA_LOKI_PUSH_URL is set")
                config = GrafanaLogSinkConfig(
                    push_to_loki=True,
                    create_annotations=False,
                    loki_push_url=loki_push_url,
                    loki_write_token=loki_write_token,
                )
                return None, config
            return None, None

        # Handle both Pydantic model and plain dict forms
        if isinstance(grafana, BaseModel):
            creds = grafana.model_dump(exclude_none=True)
        elif isinstance(grafana, dict):
            creds = dict(grafana)
        else:
            logger.debug("[resolve-sink] unrecognized grafana creds type: %s", type(grafana))
            if loki_push_url:
                config = GrafanaLogSinkConfig(
                    push_to_loki=True,
                    create_annotations=False,
                    loki_push_url=loki_push_url,
                    loki_write_token=loki_write_token,
                )
                return None, config
            return None, None

        endpoint = creds.get("endpoint") or creds.get("grafana_endpoint") or ""
        api_key = creds.get("api_key") or creds.get("grafana_api_key") or ""
        username = creds.get("username", "")
        password = creds.get("password", "")

        client = None
        create_annotations = False

        # Build client only if endpoint is present
        if endpoint:
            from integrations.grafana.client import get_grafana_client_from_credentials

            client = get_grafana_client_from_credentials(
                endpoint=endpoint,
                api_key=api_key,
                account_id="investigation_sink",
                username=username,
                password=password,
            )
            create_annotations = client is not None

        # If client is None and loki_push_url is empty -> return (None, None)
        if client is None and not loki_push_url:
            logger.debug(
                "[resolve-sink] no deliverable sink: endpoint missing, client None, "
                "and GRAFANA_LOKI_PUSH_URL not set"
            )
            return None, None

        # Build config explicitly (do not rely on defaults alone)
        config = GrafanaLogSinkConfig(
            push_to_loki=True,
            create_annotations=create_annotations,
            loki_push_url=loki_push_url,
            loki_write_token=loki_write_token,
        )

        return client, config

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        # Extract Grafana creds using the same pattern as existing tools.
        # resolved_integrations["grafana"] is a GrafanaIntegrationConfig
        # Pydantic model (or a dict in some test flows).
        resolved = state.get("resolved_integrations") or {}
        if not isinstance(resolved, dict):
            resolved = {}
        client, config = self._resolve_sink_inputs(resolved)

        if client is None and config is None:
            logger.debug("[publish] grafana delivery: nothing deliverable")
            return False

        from integrations.grafana.log_sink import GrafanaLogSink

        sink = GrafanaLogSink(client, config=config)
        return sink.send_investigation_report(state, messages=messages)


grafana_delivery_adapter = _GrafanaReportDeliveryAdapter()
register_delivery_adapter(grafana_delivery_adapter)

__all__ = ["grafana_delivery_adapter"]
