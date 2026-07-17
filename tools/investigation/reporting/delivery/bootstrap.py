"""One-shot vendor adapter loader for report delivery.

Each vendor's ``integrations/<vendor>/reporting_adapter.py`` registers a
:class:`platform.reporting.delivery_registry.ReportDeliveryAdapter` (and the
Slack module also registers a
:class:`platform.reporting.slack_reactions.SlackReactionsPort`) at import
time. This bootstrap concentrates those seven vendor imports in one place so
:mod:`tools.investigation.reporting.delivery.dispatch` — the actual dispatch
loop — stays vendor-neutral.

Historically the dispatch node itself imported each vendor's ``send_*_report``
symbol, forming a ``tools -> integrations`` edge per vendor (T-4 layering
audit, issue #3352, items 23/28). Moving the wiring into a single bootstrap
file keeps the dispatch body free of vendor logic and makes the transitional
audit surface obvious. Once every ``tools -> integrations`` edge is removed
(when the vendor adapters are triggered by a lower-layer loader — e.g. the
integrations catalog itself), this bootstrap goes away.
"""

from __future__ import annotations

from platform.reporting.delivery_registry import registered_delivery_adapter_names


def ensure_delivery_adapters_registered() -> tuple[str, ...]:
    """Import every vendor adapter module so its side-effect registration runs.

    Returns the names of currently registered adapters as a small
    diagnostics affordance (empty tuple means the bootstrap silently produced
    zero adapters, which should never happen and is worth surfacing in tests).
    """
    # These imports are intentional wiring, not dispatch logic. Keeping them
    # here means ``dispatch.py`` never touches the vendor packages directly.
    import integrations.discord.reporting_adapter  # noqa: F401
    import integrations.openclaw.reporting_adapter  # noqa: F401
    import integrations.rocketchat.reporting_adapter  # noqa: F401
    import integrations.slack.reporting_adapter  # noqa: F401
    import integrations.telegram.reporting_adapter  # noqa: F401
    import integrations.twilio.reporting_adapter  # noqa: F401
    import integrations.whatsapp.reporting_adapter  # noqa: F401

    return registered_delivery_adapter_names()


__all__ = ["ensure_delivery_adapters_registered"]
