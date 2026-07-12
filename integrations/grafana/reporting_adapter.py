"""Grafana ``ReportDeliveryAdapter`` implementation.

Registers into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.grafana`` directly (T-4 layering audit).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from platform.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _GrafanaReportDeliveryAdapter:
    """Grafana delivery adapter — pushes investigation logs/annotations."""

    name = "grafana"

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
        grafana = (
            resolved.get("grafana") or resolved.get("grafana_local")
            if isinstance(resolved, dict)
            else None
        )
        if not grafana:
            logger.debug("[publish] grafana delivery: no grafana integration configured")
            return False

        # Handle both Pydantic model and plain dict forms
        if isinstance(grafana, BaseModel):
            creds = grafana.model_dump(exclude_none=True)
        elif isinstance(grafana, dict):
            creds = dict(grafana)
        else:
            logger.debug("[publish] grafana delivery: unrecognized creds type %s", type(grafana))
            return False

        endpoint = creds.get("endpoint") or creds.get("grafana_endpoint") or ""
        api_key = creds.get("api_key") or creds.get("grafana_api_key") or ""
        username = creds.get("username", "")
        password = creds.get("password", "")

        if not endpoint:
            logger.debug("[publish] grafana delivery: no endpoint configured")
            return False

        from integrations.grafana.client import get_grafana_client_from_credentials
        from integrations.grafana.log_sink import GrafanaLogSink

        client = get_grafana_client_from_credentials(
            endpoint=endpoint,
            api_key=api_key,
            account_id="investigation_sink",
            username=username,
            password=password,
        )

        sink = GrafanaLogSink(client)
        sink.send_investigation_report(state, messages=messages)
        return True


grafana_delivery_adapter = _GrafanaReportDeliveryAdapter()
register_delivery_adapter(grafana_delivery_adapter)

__all__ = ["grafana_delivery_adapter"]
