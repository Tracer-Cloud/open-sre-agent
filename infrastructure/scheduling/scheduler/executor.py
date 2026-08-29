"""Task execution with claim-based dedup and per-provider delivery."""

from __future__ import annotations

import json
import logging

from infrastructure.scheduling.scheduler.claim_store import complete_run, try_claim
from infrastructure.scheduling.scheduler.credentials import (
    resolve_slack_credentials,
    resolve_telegram_default_chat_id,
)
from infrastructure.scheduling.scheduler.delivery import resolve_slack_delivery_chat_id
from infrastructure.scheduling.scheduler.delivery_bundle import resolve_delivery_adapter
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_TELEGRAM_CHAT_ID_PARAM,
)
from infrastructure.scheduling.scheduler.operation_log import record_scheduler_execution_operation
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.tasks import build_message
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind, TaskStatus

logger = logging.getLogger(__name__)


def execute_task(
    task: ScheduledTask,
    fire_time: str,
    runners: SchedulerRunners,
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
        record_scheduler_execution_operation(
            "scheduled_task_execution_skipped",
            task,
            fire_time=fire_time,
            status=TaskStatus.SKIPPED,
            extra={"reason": "already_claimed"},
        )
        return False

    logger.info("Executing task %s (kind=%s, fire_time=%s)", task.id, task.kind, fire_time)
    record_scheduler_execution_operation(
        "scheduled_task_execution_started",
        task,
        fire_time=fire_time,
        status=TaskStatus.RUNNING,
    )
    _emit_analytics_started(task)

    # Build the message
    try:
        message = build_message(task, runners)
    except RuntimeError as exc:
        # Pipeline failures — record without leaking details to chat
        _record_failure(task, fire_time, str(exc), stage="message_build")
        return False
    except Exception as exc:
        _record_failure(
            task,
            fire_time,
            f"Message build error: {type(exc).__name__}",
            stage="message_build",
        )
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
        record_scheduler_execution_operation(
            "scheduled_task_execution_completed",
            task,
            fire_time=fire_time,
            status=TaskStatus.SUCCESS,
            message_chars=0,
            extra={"delivery_skipped": True},
        )
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
        record_scheduler_execution_operation(
            "scheduled_task_execution_completed",
            task,
            fire_time=fire_time,
            status=TaskStatus.SUCCESS,
            message_chars=len(message),
            message_id=message_id,
            error=error,
            extra={
                "delivery_skipped": False,
                "partial_failure": bool(error),
            },
        )
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
        _record_failure(task, fire_time, error, stage="delivery", message_chars=len(message))
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
    """Deliver one message to ``task.provider`` via its installed adapter."""
    adapter = resolve_delivery_adapter(task.provider)
    if adapter is None:
        return False, f"Unsupported provider: {task.provider}", ""
    return adapter.deliver(task, message)


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
        slack_creds = resolve_slack_credentials(task.params)
        chat_id = resolve_slack_delivery_chat_id(
            task,
            webhook_url=str(slack_creds.get("webhook_url") or ""),
        )
    elif provider == Provider.INTERACTIVE_SHELL:
        chat_id = ""
    return task.model_copy(update={"provider": provider, "chat_id": chat_id})


def _run_provider_label(task: ScheduledTask) -> str:
    """Return the provider label persisted with run history."""
    channels = task.params.get(LOOP_CHANNELS_PARAM, "").strip()
    return channels or task.provider.value


def _record_failure(
    task: ScheduledTask,
    fire_time: str,
    error: str,
    *,
    stage: str,
    message_chars: int | None = None,
) -> None:
    """Record a failed execution in the claim store and emit analytics."""
    complete_run(
        task.id,
        fire_time,
        status=TaskStatus.FAILED,
        error=error,
        provider=_run_provider_label(task),
    )
    _emit_analytics(task, TaskStatus.FAILED, error=error)
    record_scheduler_execution_operation(
        "scheduled_task_execution_failed",
        task,
        fire_time=fire_time,
        status=TaskStatus.FAILED,
        message_chars=message_chars,
        error=error,
        extra={"stage": stage},
    )
    logger.warning("Task %s failed: %s", task.id, error)


def _emit_analytics_started(task: ScheduledTask) -> None:
    """Emit SCHEDULED_TASK_STARTED event after a claim is won."""
    try:
        from infrastructure.analytics.events import Event
        from infrastructure.analytics.provider import Properties, get_analytics

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
        from infrastructure.analytics.events import Event
        from infrastructure.analytics.provider import Properties, get_analytics

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
