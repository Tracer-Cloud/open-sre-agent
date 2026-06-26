"""Dispatch rendered reports to configured publish channels."""

from __future__ import annotations

import logging
from typing import Any

from core.domain.state import InvestigationState
from core.orchestration.node.publish_findings.delivery.slack import deliver_slack_report
from core.orchestration.node.publish_findings.formatters.messages import ReportMessages
from core.orchestration.node.publish_findings.gitlab_writeback import post_gitlab_mr_writeback

logger = logging.getLogger(__name__)


def dispatch_report(
    state: InvestigationState,
    messages: ReportMessages,
    *,
    investigation_id: str | None,
    investigation_url: str | None,
) -> list[dict]:
    """Dispatch report messages to all configured channels.

    Returns the Slack blocks sent, including the investigation action blocks.
    """
    from platform.notifications.slack_delivery import build_action_blocks

    all_blocks = messages.slack_blocks + build_action_blocks(
        investigation_url or "", investigation_id
    )
    deliver_slack_report(state, messages.slack_text, all_blocks)

    resolved = state.get("resolved_integrations") or {}
    discord_creds = resolved.get("discord", {})
    logger.debug(
        "[publish] discord creds present=%s keys=%s",
        bool(discord_creds),
        list(discord_creds.keys()) if discord_creds else [],
    )
    _dispatch_discord(state, messages.slack_text, discord_creds)
    _dispatch_telegram(state, messages.telegram_html, resolved.get("telegram", {}))
    _dispatch_whatsapp(state, messages.whatsapp_text, resolved.get("whatsapp", {}))
    _dispatch_twilio_sms(state, messages.sms_text, resolved.get("twilio", {}))
    _dispatch_openclaw(state, messages.slack_text, resolved.get("openclaw", {}))
    post_gitlab_mr_writeback(state, messages.slack_text)
    return all_blocks


def _dispatch_discord(
    state: InvestigationState,
    slack_message: str,
    discord_creds: dict[str, Any],
) -> None:
    if not discord_creds:
        logger.debug("[publish] discord delivery: no discord integration configured")
        return

    from platform.notifications.discord_delivery import send_discord_report

    discord_ctx = state.get("discord_context") or {}
    bot_token = discord_ctx.get("bot_token") or discord_creds.get("bot_token", "")
    channel_id = discord_ctx.get("channel_id") or discord_creds.get("default_channel_id", "")
    thread_id = discord_ctx.get("thread_id", "")
    logger.debug(
        "[publish] discord delivery: channel_id=%s thread_id=%s bot_token_present=%s",
        channel_id,
        thread_id,
        bool(bot_token),
    )
    if bot_token and channel_id:
        discord_posted, discord_error = send_discord_report(
            slack_message,
            {"bot_token": bot_token, "channel_id": channel_id, "thread_id": thread_id},
        )
        logger.debug(
            "[publish] discord delivery: posted=%s error=%s", discord_posted, discord_error
        )
        if not discord_posted:
            logger.warning(
                "[publish] Discord delivery failed: channel=%s error=%s",
                channel_id,
                discord_error,
            )
        return

    logger.debug(
        "[publish] discord delivery: skipped - bot_token_present=%s channel_id=%s",
        bool(bot_token),
        channel_id,
    )


def _dispatch_telegram(
    state: InvestigationState,
    telegram_message: str,
    telegram_creds: dict[str, Any],
) -> None:
    if not telegram_creds:
        logger.debug("[publish] telegram delivery: no telegram integration configured")
        return

    from platform.notifications.telegram_delivery import send_telegram_report

    telegram_ctx = state.get("telegram_context") or {}
    bot_token = telegram_ctx.get("bot_token") or telegram_creds.get("bot_token", "")
    chat_id = telegram_ctx.get("chat_id") or telegram_creds.get("default_chat_id", "")
    reply_to = str(telegram_ctx.get("reply_to_message_id") or "")
    logger.debug(
        "[publish] telegram delivery: chat_id=%s reply_to=%s bot_token_present=%s",
        chat_id,
        reply_to,
        bool(bot_token),
    )
    if bot_token and chat_id:
        tg_posted, tg_error = send_telegram_report(
            telegram_message,
            {"bot_token": bot_token, "chat_id": chat_id, "reply_to_message_id": reply_to},
        )
        logger.debug("[publish] telegram delivery: posted=%s error=%s", tg_posted, tg_error)
        if not tg_posted:
            logger.warning(
                "[publish] Telegram delivery failed: chat_id=%s error=%s",
                chat_id,
                tg_error,
            )
        return

    logger.debug(
        "[publish] telegram delivery: skipped - bot_token_present=%s chat_id=%s",
        bool(bot_token),
        chat_id,
    )


def _dispatch_whatsapp(
    state: InvestigationState,
    whatsapp_message: str,
    whatsapp_creds: dict[str, Any],
) -> None:
    if not whatsapp_creds:
        logger.debug("[publish] whatsapp delivery: no whatsapp integration configured")
        return

    from platform.notifications.whatsapp_delivery import send_whatsapp_report

    whatsapp_ctx: dict[str, Any] = state.get("whatsapp_context") or {}
    account_sid = whatsapp_ctx.get("account_sid") or whatsapp_creds.get("account_sid", "")
    auth_token = whatsapp_ctx.get("auth_token") or whatsapp_creds.get("auth_token", "")
    from_number = whatsapp_ctx.get("from_number") or whatsapp_creds.get("from_number", "")
    to = whatsapp_ctx.get("to") or whatsapp_creds.get("default_to", "")
    logger.debug(
        "[publish] whatsapp delivery: to=%s account_sid=%s auth_token_present=%s from_number=%s",
        to,
        account_sid,
        bool(auth_token),
        from_number,
    )
    if account_sid and auth_token and from_number and to:
        wa_posted, wa_error = send_whatsapp_report(
            whatsapp_message,
            {
                "account_sid": account_sid,
                "auth_token": auth_token,
                "from_number": from_number,
                "to": to,
            },
        )
        logger.debug("[publish] whatsapp delivery: posted=%s error=%s", wa_posted, wa_error)
        if not wa_posted:
            logger.warning(
                "[publish] WhatsApp delivery failed: to=%s error=%s",
                to,
                wa_error,
            )
        return

    logger.debug(
        "[publish] whatsapp delivery: skipped - account_sid_present=%s "
        "auth_token_present=%s from_number_present=%s to_present=%s",
        bool(account_sid),
        bool(auth_token),
        bool(from_number),
        bool(to),
    )


def _dispatch_twilio_sms(
    state: InvestigationState,
    sms_message: str,
    twilio_creds: dict[str, Any],
) -> None:
    if not twilio_creds:
        logger.debug("[publish] twilio delivery: no twilio integration configured")
        return

    sms_cfg = twilio_creds.get("sms") or {}
    if not sms_cfg.get("enabled"):
        return

    from platform.notifications.twilio_delivery import send_twilio_sms_report

    twilio_sms_ctx: dict[str, Any] = state.get("twilio_sms_context") or {}
    sms_to = twilio_sms_ctx.get("to") or sms_cfg.get("default_to") or ""
    sms_from = sms_cfg.get("from_number", "")
    messaging_service_sid = sms_cfg.get("messaging_service_sid", "")
    account_sid = twilio_creds.get("account_sid", "")
    auth_token = twilio_creds.get("auth_token", "")
    logger.debug(
        "[publish] twilio sms delivery: to=%s from=%s msg_service=%s account_sid_present=%s",
        sms_to,
        sms_from,
        messaging_service_sid,
        bool(account_sid),
    )
    if account_sid and auth_token and sms_to and (sms_from or messaging_service_sid):
        sms_ok, sms_error, sms_sid = send_twilio_sms_report(
            sms_message,
            {
                "account_sid": account_sid,
                "auth_token": auth_token,
                "from_number": sms_from,
                "messaging_service_sid": messaging_service_sid,
                "to": sms_to,
            },
        )
        logger.debug(
            "[publish] twilio sms delivery: posted=%s sid=%s error=%s",
            sms_ok,
            sms_sid,
            sms_error,
        )
        if not sms_ok:
            logger.warning(
                "[publish] Twilio SMS delivery failed: to=%s error=%s",
                sms_to,
                sms_error,
            )
        return

    logger.warning(
        "[publish] twilio sms delivery: skipped - SMS channel is enabled "
        "but not deliverable (recipient_present=%s sender_present=%s "
        "account_sid_present=%s auth_token_present=%s). "
        "Set TWILIO_SMS_DEFAULT_TO to enable auto-delivery.",
        bool(sms_to),
        bool(sms_from or messaging_service_sid),
        bool(account_sid),
        bool(auth_token),
    )


def _dispatch_openclaw(
    state: InvestigationState,
    slack_message: str,
    openclaw_creds: dict[str, Any],
) -> None:
    if not openclaw_creds:
        logger.debug("[publish] openclaw delivery: no openclaw integration configured")
        return

    from platform.notifications.openclaw_delivery import send_openclaw_report

    oc_posted, oc_error = send_openclaw_report(state, slack_message, openclaw_creds)
    logger.debug("[publish] openclaw delivery: posted=%s error=%s", oc_posted, oc_error)
    if not oc_posted:
        logger.debug("[publish] OpenClaw delivery failed: %s", oc_error)
