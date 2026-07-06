"""Dispatch rendered reports to every registered delivery adapter.

Each vendor supplies a
:class:`platform.reporting.delivery_registry.ReportDeliveryAdapter` from its
own ``integrations/<vendor>/reporting_adapter.py`` module. The bootstrap
helper in :mod:`tools.investigation.reporting.delivery.bootstrap` triggers
their side-effect registrations before the loop runs, so this dispatch node
never imports from ``integrations.*`` directly (T-4 layering audit, issue
#3352, items 23/28).

Slack is treated as the primary channel — it renders the shared interactive
action blocks the pipeline attaches to Slack messages and updates the
investigation thread's status emoji. All other adapters are called
opportunistically; each decides for itself whether the current
:class:`~core.state.InvestigationState` carries enough context to
deliver.
"""

from __future__ import annotations

import logging

from core.state import InvestigationState
from platform.reporting.delivery_registry import (
    ReportDeliveryAdapter,
    get_delivery_adapter,
    iter_delivery_adapters,
)
from tools.investigation.reporting.delivery.bootstrap import (
    ensure_delivery_adapters_registered,
)
from tools.investigation.reporting.formatters.messages import ReportMessages
from tools.investigation.reporting.gitlab_writeback import post_gitlab_mr_writeback

logger = logging.getLogger(__name__)


def dispatch_report(
    state: InvestigationState,
    messages: ReportMessages,
    *,
    investigation_id: str | None,
    investigation_url: str | None,
) -> list[dict]:
    """Dispatch report messages to every registered delivery channel.

    Returns the Slack blocks sent (including the investigation action blocks)
    so upstream tests can assert the shared block payload without probing each
    vendor separately.
    """
    ensure_delivery_adapters_registered()

    all_blocks = messages.slack_blocks + _build_slack_action_blocks(
        investigation_url or "", investigation_id
    )

    resolved = state.get("resolved_integrations") or {}
    if isinstance(resolved, dict):
        _log_discord_debug(resolved.get("discord", {}))

    payload = _messages_payload(messages)

    slack_adapter = get_delivery_adapter("slack")
    if slack_adapter is not None:
        _run_adapter(slack_adapter, state, messages=payload, blocks=all_blocks)

    for adapter in iter_delivery_adapters():
        if adapter.name == "slack":
            continue
        _run_adapter(adapter, state, messages=payload, blocks=all_blocks)

    post_gitlab_mr_writeback(state, messages.slack_text)
    return all_blocks


def _run_adapter(
    adapter: ReportDeliveryAdapter,
    state: InvestigationState,
    *,
    messages: dict[str, object],
    blocks: list[dict],
) -> None:
    """Invoke ``adapter.deliver`` and swallow non-fatal errors.

    Vendor adapters raise ``RuntimeError`` only when the failure must abort
    the investigation (e.g. the Slack thread that triggered the run failed to
    receive the report). Anything else is logged and does not stop other
    adapters from running.
    """
    try:
        adapter.deliver(state, messages=messages, blocks=blocks)
    except RuntimeError:
        # Fail-closed adapters (currently Slack) raise so upstream can surface
        # the error; do not swallow.
        raise
    except Exception:  # noqa: BLE001
        logger.warning(
            "[publish] %s adapter raised while delivering report",
            adapter.name,
            exc_info=True,
        )


def _messages_payload(messages: ReportMessages) -> dict[str, object]:
    """Return a plain ``dict`` view of the rendered per-channel messages.

    Adapters accept a :class:`platform.reporting.delivery_registry.DeliveryContext`
    (a ``Mapping``) so they never touch the concrete
    :class:`~tools.investigation.reporting.formatters.messages.ReportMessages`
    dataclass — that keeps the platform boundary free of ``tools`` types.
    """
    return {
        "slack_text": messages.slack_text,
        "slack_blocks": messages.slack_blocks,
        "telegram_html": messages.telegram_html,
        "whatsapp_text": messages.whatsapp_text,
        "sms_text": messages.sms_text,
    }


def _log_discord_debug(discord_creds: object) -> None:
    if isinstance(discord_creds, dict):
        logger.debug(
            "[publish] discord creds present=%s keys=%s",
            bool(discord_creds),
            list(discord_creds.keys()),
        )
    else:
        logger.debug("[publish] discord creds present=%s", bool(discord_creds))


def _build_slack_action_blocks(investigation_url: str, investigation_id: str | None) -> list[dict]:
    """Build the shared Slack action blocks without importing ``integrations.slack``.

    The Slack adapter is the canonical source for these blocks, but importing
    it directly from the dispatch node reintroduces the ``tools ->
    integrations`` edge we're removing. Instead, we ask the registered Slack
    adapter to build them: it exposes ``build_action_blocks`` as a plain
    attribute, and adapters that do not implement it (test stubs, non-Slack
    vendors) simply skip the enrichment.
    """
    slack_adapter = get_delivery_adapter("slack")
    builder = getattr(slack_adapter, "build_action_blocks", None)
    if builder is None:
        return []
    result = builder(investigation_url, investigation_id)
    return list(result) if isinstance(result, list) else []
