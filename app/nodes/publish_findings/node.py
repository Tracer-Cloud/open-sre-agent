"""Main orchestration node for report generation and publishing."""

import logging

from langsmith import traceable

from app.masking import MaskingContext
from app.nodes.publish_findings.formatters.report import build_slack_blocks, format_slack_message
from app.nodes.publish_findings.gitlab_writeback import post_gitlab_mr_writeback
from app.nodes.publish_findings.renderers.editor import open_in_editor
from app.nodes.publish_findings.renderers.terminal import render_report
from app.nodes.publish_findings.report_context import build_report_context
from app.state import InvestigationState
from app.types.config import NodeConfig
from app.utils.ingest_delivery import create_investigation_and_attach_url

logger = logging.getLogger(__name__)


def generate_report(state: InvestigationState) -> dict:
    """Generate and publish the final RCA report."""
    from app.utils.slack_delivery import build_action_blocks, send_slack_report

    ctx = build_report_context(state)
    short_summary = state.get("problem_md")
    slack_message = format_slack_message(ctx)

    # Restore any masked infrastructure identifiers in user-facing output.
    # No-op when masking is disabled or the state has no placeholders.
    masking_ctx = MaskingContext.from_state(dict(state))
    slack_message = masking_ctx.unmask(slack_message)
    if isinstance(short_summary, str):
        short_summary = masking_ctx.unmask(short_summary)

    investigation_id, investigation_url = create_investigation_and_attach_url(
        state,
        slack_message,
        short_summary,
    )

    all_blocks = build_slack_blocks(ctx) + build_action_blocks(investigation_url, investigation_id)
    all_blocks = masking_ctx.unmask_value(all_blocks)
    render_report(slack_message, root_cause_category=state.get("root_cause_category"))
    open_in_editor(slack_message)

    slack_ctx = state.get("slack_context", {})
    thread_ts = slack_ctx.get("thread_ts") or slack_ctx.get("ts")
    _channel = slack_ctx.get("channel_id")
    _token = slack_ctx.get("access_token")
    _alert_ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")

    resolved = state.get("resolved_integrations") or {}
    discord_creds = resolved.get("discord", {})
    logger.debug("[publish] slack_ctx=%s", slack_ctx)
    logger.debug(
        "[publish] discord creds present=%s keys=%s",
        bool(discord_creds),
        list(discord_creds.keys()) if discord_creds else [],
    )

    report_posted, delivery_error = send_slack_report(
        slack_message,
        channel=_channel,
        thread_ts=thread_ts,
        access_token=_token,
        blocks=all_blocks,
    )

    logger.debug(
        "[publish] slack delivery: posted=%s channel=%s thread_ts=%s error=%s",
        report_posted,
        _channel,
        thread_ts,
        delivery_error,
    )
    if report_posted and _token and _channel and _alert_ts:
        from app.utils.slack_delivery import swap_reaction

        swap_reaction("eyes", "clipboard", _channel, _alert_ts, _token)
    elif thread_ts and not report_posted:
        raise RuntimeError(
            f"[publish] Slack delivery failed: channel={_channel}, thread_ts={thread_ts}, reason={delivery_error}"
        )

    # Secondary Notification Deliveries (Discord, Telegram, WhatsApp, etc.)
    from app.notifications.models import NotificationEvent
    from app.notifications.registry import get_registry

    # Create the structured event
    event = NotificationEvent(
        title=f"Investigation findings for {state.get('investigation_id', 'unknown')}",
        body=slack_message,
        severity="info",
        investigation_id=investigation_id,
        investigation_url=investigation_url,
        metadata={"blocks": all_blocks},
    )

    notification_registry = get_registry()

    for service_name, provider in notification_registry.get_providers().items():
        # Only dispatch if the integration is configured/resolved
        creds = resolved.get(service_name)
        if not creds:
            continue

        # Skip Slack as it was handled primary above
        if service_name == "slack":
            continue

        # Build provider-specific configuration from state and resolved creds
        provider_config = {**creds}
        if service_name == "discord":
            discord_ctx = state.get("discord_context") or {}
            provider_config.update({
                "bot_token": discord_ctx.get("bot_token") or creds.get("bot_token", ""),
                "channel_id": discord_ctx.get("channel_id") or creds.get("default_channel_id", ""),
                "thread_id": discord_ctx.get("thread_id", ""),
            })
        elif service_name == "telegram":
            telegram_ctx = state.get("telegram_context") or {}
            provider_config.update({
                "bot_token": telegram_ctx.get("bot_token") or creds.get("bot_token", ""),
                "chat_id": telegram_ctx.get("chat_id") or creds.get("default_chat_id", ""),
                "reply_to_message_id": str(telegram_ctx.get("reply_to_message_id") or ""),
            })
        elif service_name == "whatsapp":
            # WhatsApp uses webhook_url and phone_number from creds
            pass

        logger.debug("[publish] dispatching to %s", service_name)
        posted, error = provider.send_notification(event, provider_config)
        if not posted:
            logger.warning("[publish] %s delivery failed: %s", service_name.title(), error)
        else:
            logger.debug("[publish] %s delivery successful", service_name)

    post_gitlab_mr_writeback(state, slack_message)

    return {"slack_message": slack_message, "report": slack_message}


@traceable(name="node_publish_findings")
def node_publish_findings(
    state: InvestigationState,
    config: NodeConfig | None = None,  # noqa: ARG001
) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return generate_report(state)
