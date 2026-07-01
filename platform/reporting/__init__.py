"""Cross-vendor report-delivery primitives (registry + surface-agnostic ports).

The investigation pipeline in :mod:`tools.investigation.reporting.delivery`
dispatches rendered incident reports to whichever vendor channels the current
run has credentials for (Slack, Discord, Telegram, WhatsApp, Twilio SMS,
OpenClaw). Historically the dispatch logic imported each vendor's
``send_*_report`` function directly, which forms a ``tools -> integrations``
edge for every vendor (T-4 layering audit, issue #3352).

This subpackage owns the vendor-neutral seam both sides use:

* :mod:`platform.reporting.delivery_registry` — the
  :class:`ReportDeliveryAdapter` protocol and the process-scoped registry that
  vendor adapter modules register themselves into.
* :mod:`platform.reporting.slack_reactions` — a small port for Slack
  ``add_reaction`` / ``swap_reaction`` so the investigation intake stage can
  update its status emoji without importing ``integrations.slack`` directly.

Vendor adapter modules (``integrations/<vendor>/reporting_adapter.py``) call
``register_delivery_adapter`` at import time. The single point of adapter
loading lives in :mod:`tools.investigation.reporting.delivery.bootstrap`, which
the dispatch entry point invokes to populate the registry before its first
delivery loop.
"""

from __future__ import annotations

__all__: list[str] = []
