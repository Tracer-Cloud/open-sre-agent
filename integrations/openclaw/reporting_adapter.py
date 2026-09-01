"""OpenClaw ``ReportDeliveryAdapter`` implementation.

Registers itself into the platform-level delivery registry at import time so
``tools.investigation.reporting.delivery.dispatch`` never imports
``integrations.openclaw`` directly (T-4 layering audit, issue #3352).
"""

from __future__ import annotations

import logging
from typing import Any, cast

from core.state import InvestigationState
from infrastructure.delivery.reporting.delivery_registry import (
    DeliveryContext,
    register_delivery_adapter,
)

logger = logging.getLogger(__name__)


class _OpenClawReportDeliveryAdapter:
    """OpenClaw delivery adapter — forwards the Slack-rendered report to OpenClaw."""

    name = "openclaw"

    def deliver(
        self,
        state: DeliveryContext,
        *,
        messages: DeliveryContext,
        blocks: list[dict[str, Any]],  # noqa: ARG002
    ) -> bool:
        resolved = state.get("resolved_integrations") or {}
        openclaw_creds = resolved.get("openclaw") if isinstance(resolved, dict) else None
        if not openclaw_creds:
            logger.debug("[publish] openclaw delivery: no openclaw integration configured")
            return False

        from integrations.openclaw.delivery import (
            MISSING_CONVERSATION_ID,
            send_openclaw_report,
        )

        # ``state`` arrives as the platform-level ``DeliveryContext`` (a
        # ``Mapping``); the OpenClaw client wants the concrete
        # ``InvestigationState`` TypedDict, which is dict-backed at runtime.
        # Cast at the boundary — the adapter is the layer that owns the
        # bridge between vendor-neutral platform types and integration types.
        posted, error = send_openclaw_report(
            cast(InvestigationState, state),
            messages.get("slack_text", ""),
            openclaw_creds,
        )
        logger.debug("[publish] openclaw delivery: posted=%s error=%s", posted, error)
        if not posted:
            if error == MISSING_CONVERSATION_ID:
                logger.debug("[publish] openclaw delivery: skipped - %s", error)
                return False
            logger.warning("[publish] OpenClaw delivery failed: %s", error)
        return True


openclaw_delivery_adapter = _OpenClawReportDeliveryAdapter()
register_delivery_adapter(openclaw_delivery_adapter)

__all__ = ["openclaw_delivery_adapter"]
