"""Task execution with claim-based dedup and per-provider delivery."""

from __future__ import annotations

import logging
import re

from platform.scheduler.claim_store import complete_run, try_claim
from platform.scheduler.credentials import (
    resolve_discord_credentials,
    resolve_rocketchat_credentials,
    resolve_slack_credentials,
    resolve_telegram_credentials,
)
from platform.scheduler.tasks import build_message
from platform.scheduler.types import Provider, ScheduledTask, TaskStatus

logger = logging.getLogger(__name__)

# Strip HTML tags for providers that don't support them
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def execute_task(
    task: ScheduledTask,
    fire_time: str,
) -> bool:
    """Execute a scheduled task with claim-based dedup.

    Args:
        task: The scheduled task definition.
        fire_time: The canonical fire time string (UTC, minute-precision) from the
            scheduler trigger, used as the dedup key.

    Returns:
        True if the task was executed and delivered successfully.
        False if the claim was lost (another instance handled it) or delivery failed.
    """
    # Attempt to claim this execution slot
    if not try_claim(task.id, fire_time):
        logger.info(
            "Task %s fire_time=%s already claimed by another instance",
            task.id,
            fire_time,
        )
        return False

    logger.info("Executing task %s (kind=%s, fire_time=%s)", task.id, task.kind, fire_time)
    _emit_analytics_started(task)

    # Build the message
    try:
        message = build_message(task)
    except RuntimeError as exc:
        # Pipeline failures — record without leaking details to chat
        _record_failure(task, fire_time, str(exc))
        return False
    except Exception as exc:
        _record_failure(task, fire_time, f"Message build error: {type(exc).__name__}")
        return False

    # Quiet ticks (e.g. uptime watch with no transitions) skip delivery.
    if not message.strip():
        complete_run(
            task.id,
            fire_time,
            status=TaskStatus.SUCCESS,
            posted_message_id="",
            provider=task.provider.value,
        )
        _emit_analytics(task, TaskStatus.SUCCESS)
        logger.info("Task %s produced no message; delivery skipped", task.id)
        return True

    # Deliver to the configured provider
    ok, error, message_id = _deliver(task, message)

    if ok:
        complete_run(
            task.id,
            fire_time,
            status=TaskStatus.SUCCESS,
            posted_message_id=message_id,
            provider=task.provider.value,
        )
        _emit_analytics(task, TaskStatus.SUCCESS)
        logger.info("Task %s delivered successfully (message_id=%s)", task.id, message_id)
        return True
    else:
        _record_failure(task, fire_time, error)
        return False


def _deliver(
    task: ScheduledTask,
    message: str,
) -> tuple[bool, str, str]:
    """Route delivery to the appropriate provider.

    Returns (success, error, message_id).
    """
    if task.provider == Provider.TELEGRAM:
        return _deliver_telegram(task, message)
    elif task.provider == Provider.SLACK:
        return _deliver_slack(task, message)
    elif task.provider == Provider.DISCORD:
        return _deliver_discord(task, message)
    elif task.provider == Provider.ROCKETCHAT:
        return _deliver_rocketchat(task, message)
    else:
        return False, f"Unsupported provider: {task.provider}", ""


def _strip_html(text: str) -> str:
    """Strip HTML tags for providers that use plain text or Markdown."""
    return _HTML_TAG_RE.sub("", text)


def _deliver_telegram(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver via Telegram using the truncation helper then posting directly.

    Uses truncate_for_telegram_html to respect the 4096-char limit, then
    posts via post_telegram_message (no reply_to — new top-level message).
    """
    creds = resolve_telegram_credentials(task.params)
    bot_token = creds.get("bot_token", "")
    if not bot_token or not task.chat_id:
        return False, "Missing bot_token or chat_id for Telegram", ""

    from integrations.telegram.delivery import (
        post_telegram_message,
        truncate_for_telegram_html,
    )
    from integrations.telegram.formatting import markdown_to_telegram_html

    html_message = markdown_to_telegram_html(message)
    truncated = truncate_for_telegram_html(html_message, 4096, suffix="…")
    ok, error, msg_id = post_telegram_message(task.chat_id, truncated, bot_token, parse_mode="HTML")
    if ok:
        return True, "", msg_id
    return False, error, ""


def _deliver_slack(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver via Slack using the shared Slack delivery helper when possible."""
    creds = resolve_slack_credentials(task.params)
    access_token = creds.get("access_token", "")
    webhook_url = creds.get("webhook_url", "")

    # Strip HTML tags — Slack uses mrkdwn, not HTML
    plain_message = _strip_html(message)

    if access_token and task.chat_id:
        # Direct API post as a new top-level message
        from platform.notifications.delivery_transport import post_json

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "channel": task.chat_id,
            "text": plain_message,
        }
        response = post_json(
            url="https://slack.com/api/chat.postMessage",
            payload=payload,
            headers=headers,
        )
        if not response.ok:
            return False, f"Slack API error: {response.error}", ""
        if not 200 <= response.status_code < 300:
            error_text = response.text[:200] if response.text else f"HTTP {response.status_code}"
            return False, f"Slack HTTP error: {error_text}", ""
        if response.data.get("ok") is not True:
            error = response.data.get("error", "unknown")
            return False, f"Slack error: {error}", ""
        msg_ts = str(response.data.get("ts", ""))
        return True, "", msg_ts

    if webhook_url:
        from integrations.slack.delivery import send_slack_webhook_message

        ok, error = send_slack_webhook_message(
            plain_message,
            webhook_url=webhook_url,
        )
        return ok, error, ""

    if not task.chat_id:
        return False, "Missing chat_id or webhook_url for Slack delivery", ""

    return (
        False,
        "Scheduled tasks require Slack webhook_url or bot access_token",
        "",
    )


def _deliver_discord(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver via Discord using the existing report helper (handles embed truncation)."""
    creds = resolve_discord_credentials(task.params)
    bot_token = creds.get("bot_token", "")
    if not bot_token or not task.chat_id:
        return False, "Missing bot_token or channel_id for Discord", ""

    from integrations.discord.delivery import send_discord_report

    # Strip HTML tags — Discord uses embeds, not HTML
    plain_message = _strip_html(message)

    discord_ctx = {
        "channel_id": task.chat_id,
        "bot_token": bot_token,
        # No thread_id — scheduled deliveries post to the channel directly
    }
    ok, error = send_discord_report(plain_message, discord_ctx)
    return ok, error, ""


def _deliver_rocketchat(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver via Rocket.Chat's REST API to the task's channel.

    Requires token credentials (server_url + auth_token + user_id): scheduled
    tasks carry an explicit ``chat_id`` destination, which an incoming
    webhook's fixed destination cannot honor — so the webhook mode is
    deliberately not used here (same rule as the rocketchat_send_message
    tool).
    """
    creds = resolve_rocketchat_credentials(task.params)
    server_url = creds.get("server_url", "")
    auth_token = creds.get("auth_token", "")
    user_id = creds.get("user_id", "")
    if not (server_url and auth_token and user_id):
        if creds.get("webhook_url"):
            return (
                False,
                "Rocket.Chat scheduled delivery targets an explicit channel, which "
                "needs token credentials (server_url, auth_token, user_id); the "
                "incoming webhook's destination is fixed and cannot honor chat_id",
                "",
            )
        return False, "Missing server_url, auth_token, or user_id for Rocket.Chat", ""
    if not task.chat_id:
        return False, "Missing chat_id (channel) for Rocket.Chat", ""

    from integrations.rocketchat.delivery import post_rocketchat_message
    from platform.common.truncation import truncate
    from platform.notifications.limits import MAX_MESSAGE_SIZE

    # Strip HTML tags — Rocket.Chat uses Markdown, not HTML
    plain_message = truncate(_strip_html(message), MAX_MESSAGE_SIZE, suffix="…")
    ok, error, msg_id = post_rocketchat_message(
        server_url,
        task.chat_id,
        plain_message,
        auth_token,
        user_id,
    )
    return (True, "", msg_id) if ok else (False, error, "")


def _record_failure(task: ScheduledTask, fire_time: str, error: str) -> None:
    """Record a failed execution in the claim store and emit analytics."""
    complete_run(
        task.id,
        fire_time,
        status=TaskStatus.FAILED,
        error=error,
        provider=task.provider.value,
    )
    _emit_analytics(task, TaskStatus.FAILED, error=error)
    logger.warning("Task %s failed: %s", task.id, error)


def _emit_analytics_started(task: ScheduledTask) -> None:
    """Emit SCHEDULED_TASK_STARTED event after a claim is won."""
    try:
        from platform.analytics.events import Event
        from platform.analytics.provider import Properties, get_analytics

        properties: Properties = {
            "task_id": task.id,
            "task_kind": task.kind.value,
            "provider": task.provider.value,
        }
        get_analytics().capture(Event.SCHEDULED_TASK_STARTED, properties)
    except Exception:
        logger.debug("Failed to emit analytics for task %s", task.id, exc_info=True)


def _emit_analytics(task: ScheduledTask, status: TaskStatus, error: str = "") -> None:
    """Emit analytics event for task execution completion."""
    try:
        from platform.analytics.events import Event
        from platform.analytics.provider import Properties, get_analytics

        event_name = (
            Event.SCHEDULED_TASK_COMPLETED
            if status == TaskStatus.SUCCESS
            else Event.SCHEDULED_TASK_FAILED
        )
        properties: Properties = {
            "task_id": task.id,
            "task_kind": task.kind.value,
            "provider": task.provider.value,
            "status": status.value,
        }
        if error:
            properties["error"] = error[:200]
        get_analytics().capture(event_name, properties)
    except Exception:
        # Analytics must never crash the scheduler
        logger.debug("Failed to emit analytics for task %s", task.id, exc_info=True)


def deliver_scheduled_message(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver an ad-hoc message using the task's configured provider/chat.

    Used for one-shot notices (e.g. uptime watch activation) outside a cron tick.
    Returns ``(ok, error, message_id)``.
    """
    return _deliver(task, message)


__all__ = ["deliver_scheduled_message", "execute_task"]
