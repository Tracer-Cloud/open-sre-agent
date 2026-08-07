"""Task execution with claim-based dedup and per-provider delivery."""

from __future__ import annotations

import json
import logging
import re

from platform.scheduler.claim_store import complete_run, try_claim
from platform.scheduler.credentials import (
    resolve_discord_credentials,
    resolve_rocketchat_credentials,
    resolve_slack_credentials,
    resolve_telegram_credentials,
    resolve_telegram_default_chat_id,
)
from platform.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_SLACK_CHAT_ID_PARAM,
    LOOP_TELEGRAM_CHAT_ID_PARAM,
)
from platform.scheduler.tasks import build_message
from platform.scheduler.types import Provider, ScheduledTask, TaskKind, TaskStatus

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
            provider=_run_provider_label(task),
        )
        _emit_analytics(task, TaskStatus.SUCCESS)
        logger.info("Task %s produced no message; delivery skipped", task.id)
        return True

    # Deliver to the configured provider, or fan out when delivery_targets are present.
    ok, error, message_id = _deliver_all(task, message)

    if ok:
        complete_run(
            task.id,
            fire_time,
            status=TaskStatus.SUCCESS,
            posted_message_id=message_id,
            error=error,
            provider=_run_provider_label(task),
        )
        _emit_analytics(task, TaskStatus.SUCCESS, error=error)
        _record_work_item_reminder_delivery(task)
        if error:
            logger.warning(
                "Task %s delivered with partial channel failures (message_id=%s): %s",
                task.id,
                message_id,
                error,
            )
        else:
            logger.info("Task %s delivered successfully (message_id=%s)", task.id, message_id)
        return True
    else:
        _record_failure(task, fire_time, error)
        return False


def _record_work_item_reminder_delivery(task: ScheduledTask) -> None:
    """Persist ``last_reminded_at`` only after a reminder was delivered."""
    if task.kind is not TaskKind.WORK_ITEM_REMINDER:
        return
    item_id = task.params.get("work_item_id", "").strip()
    if not item_id:
        return
    from pathlib import Path

    from core.domain.work_items import now_iso, set_work_item_last_reminded

    store_path_text = task.params.get("store_path", "").strip()
    store_path = Path(store_path_text).expanduser() if store_path_text else None
    try:
        set_work_item_last_reminded(item_id, now_iso(), store_path=store_path)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to record last_reminded_at for work item %s after delivery",
            item_id,
            exc_info=True,
        )


def _deliver(
    task: ScheduledTask,
    message: str,
) -> tuple[bool, str, str]:
    """Route delivery to the appropriate provider.

    Returns (success, error, message_id).
    """
    delivery_providers, parse_error = _loop_delivery_providers(task)
    if parse_error:
        return False, parse_error, ""
    if delivery_providers:
        return _deliver_to_providers(task, message, delivery_providers)
    return _deliver_single(task, message)


def _deliver_single(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver one message to ``task.provider``."""
    if task.provider == Provider.TELEGRAM:
        return _deliver_telegram(task, message)
    elif task.provider == Provider.SLACK:
        return _deliver_slack(task, message)
    elif task.provider == Provider.DISCORD:
        return _deliver_discord(task, message)
    elif task.provider == Provider.ROCKETCHAT:
        return _deliver_rocketchat(task, message)
    elif task.provider == Provider.INTERACTIVE_SHELL:
        return _deliver_interactive_shell(task, message)
    else:
        return False, f"Unsupported provider: {task.provider}", ""


def _deliver_all(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    targets = _delivery_targets_for_task(task)
    if len(targets) == 1:
        return _deliver(task, message)

    failures: list[str] = []
    message_ids: list[str] = []
    for provider, chat_id in targets:
        target_task = task.model_copy(update={"provider": provider, "chat_id": chat_id})
        ok, error, message_id = _deliver(target_task, message)
        if ok:
            if message_id:
                message_ids.append(f"{provider.value}:{chat_id or '<default>'}:{message_id}")
            else:
                message_ids.append(f"{provider.value}:{chat_id or '<default>'}")
            continue
        failures.append(f"{provider.value}:{chat_id or '<default>'}: {error}")

    if failures:
        return False, "; ".join(failures), ",".join(message_ids)
    return True, "", ",".join(message_ids)


def _delivery_targets_for_task(task: ScheduledTask) -> tuple[tuple[Provider, str], ...]:
    raw_targets = task.params.get("delivery_targets", "").strip()
    targets: list[tuple[Provider, str]] = []
    if raw_targets:
        try:
            parsed = json.loads(raw_targets)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                provider_text = str(entry.get("provider", "")).strip().lower()
                if not provider_text:
                    continue
                try:
                    provider = Provider(provider_text)
                except ValueError:
                    continue
                targets.append((provider, str(entry.get("chat_id", "")).strip()))
    if not targets:
        targets.append((task.provider, task.chat_id))

    seen: set[tuple[Provider, str]] = set()
    unique: list[tuple[Provider, str]] = []
    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        unique.append(target)
    return tuple(unique)


def _loop_delivery_providers(task: ScheduledTask) -> tuple[tuple[Provider, ...], str]:
    """Return fan-out providers requested by loop metadata."""
    raw = task.params.get(LOOP_CHANNELS_PARAM, "").strip()
    if not raw:
        return (), ""

    providers: list[Provider] = []
    seen: set[Provider] = set()
    for item in raw.split(","):
        value = item.strip().lower()
        if not value:
            continue
        try:
            provider = Provider(value)
        except ValueError:
            return (), f"Unsupported loop delivery channel: {value}"
        if provider not in seen:
            providers.append(provider)
            seen.add(provider)
    if not providers:
        return (), "Loop delivery channel list is empty"
    return tuple(providers), ""


def _deliver_to_providers(
    task: ScheduledTask,
    message: str,
    providers: tuple[Provider, ...],
) -> tuple[bool, str, str]:
    """Fan out one built message to every requested provider.

    Retries destinations that fail on the first pass so a transient outage on
    one channel does not permanently miss the tick. When at least one channel
    succeeds, the claim completes as success and failed destinations are
    surfaced in the error field (callers can ``/loops run`` for a fresh
    second-precision delivery of the remaining channels).
    """
    pending = list(providers)
    message_ids: list[str] = []
    errors: list[str] = []
    for attempt in range(3):
        if not pending:
            break
        still_pending: list[Provider] = []
        for provider in pending:
            delivery_task = _task_for_delivery_provider(task, provider)
            ok, error, message_id = _deliver_single(delivery_task, message)
            if ok:
                message_ids.append(
                    f"{provider.value}:{message_id}" if message_id else provider.value
                )
                continue
            if attempt < 2:
                still_pending.append(provider)
            else:
                errors.append(f"{provider.value}: {error}")
        pending = still_pending

    if message_ids and not errors:
        return True, "", ", ".join(message_ids)
    if message_ids and errors:
        return True, f"partial delivery: {'; '.join(errors)}", ", ".join(message_ids)
    return False, "; ".join(errors), ""


def _task_for_delivery_provider(task: ScheduledTask, provider: Provider) -> ScheduledTask:
    """Return a task-shaped view carrying the destination for ``provider``."""
    chat_id = task.chat_id
    if provider == Provider.TELEGRAM:
        chat_id = (
            task.params.get(LOOP_TELEGRAM_CHAT_ID_PARAM, "").strip()
            or (task.chat_id if task.provider == Provider.TELEGRAM else "")
            or resolve_telegram_default_chat_id(task.params)
        )
    elif provider == Provider.SLACK:
        chat_id = task.params.get(LOOP_SLACK_CHAT_ID_PARAM, "").strip() or (
            task.chat_id if task.provider == Provider.SLACK else ""
        )
    elif provider == Provider.INTERACTIVE_SHELL:
        chat_id = ""
    return task.model_copy(update={"provider": provider, "chat_id": chat_id})


def _run_provider_label(task: ScheduledTask) -> str:
    """Return the provider label persisted with run history."""
    channels = task.params.get(LOOP_CHANNELS_PARAM, "").strip()
    return channels or task.provider.value


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
    from platform.scheduler.delivery import slack_can_deliver

    creds = resolve_slack_credentials(task.params)
    access_token = str(creds.get("access_token") or "").strip()
    webhook_url = str(creds.get("webhook_url") or "").strip()
    chat_id = (task.chat_id or "").strip()

    # Same policy as readiness: chat_id → bot token; empty chat_id → webhook OK.
    if not slack_can_deliver(creds, chat_id=chat_id):
        if chat_id and webhook_url and not access_token:
            return (
                False,
                "Slack bot token required when chat_id is set (a webhook alone "
                "cannot target an explicit chat_id)",
                "",
            )
        if not chat_id:
            return False, "Missing chat_id or webhook_url for Slack delivery", ""
        return False, "Scheduled tasks require Slack bot access_token for chat_id delivery", ""

    # Strip HTML tags — Slack uses mrkdwn, not HTML
    plain_message = _strip_html(message)

    if access_token and chat_id:
        # Direct API post as a new top-level message
        from platform.notifications.delivery_transport import post_json

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "channel": chat_id,
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

    # Channel-bound webhook path (no explicit chat_id).
    from integrations.slack.delivery import send_slack_webhook_message

    ok, error = send_slack_webhook_message(
        plain_message,
        webhook_url=webhook_url,
    )
    return ok, error, ""


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


def _deliver_interactive_shell(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Persist loop output to the local interactive-shell inbox."""
    try:
        from platform.scheduler.local_delivery import record_loop_message

        message_id = record_loop_message(task, _strip_html(message))
        return True, "", message_id
    except Exception as exc:
        logger.warning("Interactive-shell loop delivery failed for task %s: %s", task.id, exc)
        return False, f"Interactive-shell delivery error: {type(exc).__name__}", ""


def _record_failure(task: ScheduledTask, fire_time: str, error: str) -> None:
    """Record a failed execution in the claim store and emit analytics."""
    complete_run(
        task.id,
        fire_time,
        status=TaskStatus.FAILED,
        error=error,
        provider=_run_provider_label(task),
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
