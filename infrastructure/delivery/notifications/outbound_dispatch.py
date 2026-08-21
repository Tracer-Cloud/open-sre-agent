"""Dispatch a background-RCA completion notice to the channels a user chose.

The caller must have run ``bootstrap.adapters.install_notification_adapters``
first. This module cannot: ``bootstrap`` sits above ``platform`` in the layering
contract.
"""

from __future__ import annotations

import logging

from infrastructure.delivery.notifications.outbound_registry import (
    BACKGROUND_RCA,
    get_outbound_adapter,
)
from infrastructure.scheduling.background_investigations.types import BackgroundInvestigationRecord

logger = logging.getLogger(__name__)


def dispatch_background_notifications(
    *,
    record: BackgroundInvestigationRecord,
    channels: tuple[str, ...],
) -> dict[str, str]:
    """Deliver ``record`` to each channel in ``channels``; return per-channel outcomes.

    Iterates the caller's tuple, not the registry, so the mapping keeps the order
    the user configured; ``/background show`` renders it in that order.

    An adapter that raises becomes that channel's outcome and nothing more, the
    way ``_run_adapter`` treats a report adapter. Letting it out instead would
    skip every later channel and reach the runner's failure handler, which
    rewrites an already completed RCA to ``failed``. No ``RuntimeError`` re-raise
    here: the report path keeps one for fail-closed Slack, and no outbound
    channel is fail-closed.
    """
    results: dict[str, str] = {}
    for channel in channels:
        adapter = get_outbound_adapter(channel)
        if adapter is None or BACKGROUND_RCA not in adapter.capabilities:
            results[channel] = "unsupported"
            continue
        try:
            results[channel] = adapter.deliver(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[outbound] %s adapter raised while delivering", channel, exc_info=True)
            results[channel] = f"failed: {type(exc).__name__}"
    return results


__all__ = ["dispatch_background_notifications"]
