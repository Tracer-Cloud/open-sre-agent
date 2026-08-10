"""Detached investigation launcher for chat platforms."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import copy_context
from typing import Any

from config.scope_context import current_principal, current_scope
from core.tool_framework.utils.call_identity import current_tool_call_id
from gateway.core.chat import (
    ChatDeliveryTarget,
    DetachedInvestigationAck,
    get_chat_notifier_registry,
    get_current_delivery_target,
)
from gateway.core.storage.investigations.store import (
    InvestigationOrigin,
    InvestigationStore,
)
from tools.interactive_shell.shared.detached_launch import (
    DetachedLaunchResult,
    bound_detached_launcher,
)

from .launch_record import (
    DetachedLaunchRecord,
    bound_detached_launch_record,
    current_detached_launch_record,
)

logger = logging.getLogger(__name__)

# How much of the user's request is echoed into the record's display name.
_ALERT_NAME_CHARS = 50


def launch_detached_investigation(
    alert_text: str,
    *,
    context_overrides: dict[str, Any] | None = None,
) -> DetachedLaunchResult:
    """Launch a detached investigation and return immediate acknowledgment.

    The investigation runs on a background worker and posts results back to the
    originating chat thread. This is the convergence point for all three
    entrypoints (action tool, literal slash, affirmative follow-up).
    """
    # Lazy imports to avoid cycles and make patches durable
    from config.constants.datastore import database_dsn

    # Get delivery target from current context
    delivery_target = get_current_delivery_target()
    if delivery_target is None:
        return DetachedLaunchResult(
            investigation_id="",
            accepted=False,
            refusal_reason="No delivery target bound in context",
        )

    # Refuse if no notifier is registered for this platform
    registry = get_chat_notifier_registry()
    notifier = registry.get(delivery_target.platform)
    if notifier is None:
        return DetachedLaunchResult(
            investigation_id="",
            accepted=False,
            refusal_reason=f"Investigations not supported on {delivery_target.platform}",
        )

    store = _get_store()

    summary = alert_text.strip()

    # The worker runs on a thread this turn's context never reaches, so the scope
    # travels in the record rather than in a contextvar. Read ``scope.actor.id``,
    # not ``getattr(scope, "actor_id", "")`` — ``StorageScope`` has no such
    # attribute, so the defaulting form silently attributed every detached run to
    # the worker instead of the person who asked.
    scope_data = {"org_id": "", "actor_id": ""}
    scope = current_scope()
    if scope and scope.principal:
        scope_data["org_id"] = scope.principal.id
        scope_data["actor_id"] = scope.actor.id if scope.actor else ""

    trigger = {
        "raw_alert": {"alert_text": alert_text},
        "alert_name": f"chat:{summary[:_ALERT_NAME_CHARS]}"
        + ("…" if len(summary) > _ALERT_NAME_CHARS else ""),
        "delivery_target": {
            "platform": delivery_target.platform,
            "channel_id": delivery_target.channel_id,
            "thread_ts": delivery_target.thread_ts,
            "user_id": delivery_target.user_id,
            "origin_message_id": delivery_target.origin_message_id,
        },
        "context_overrides": context_overrides,
        "scope": scope_data,
    }

    # The record is owned by the org that asked for it, so a later poll or audit
    # attributes it correctly. Unbound is not reachable from a gateway turn.
    principal = current_principal()
    clerk_org_id = principal.id if principal else "chat"

    record = store.create(
        clerk_org_id=clerk_org_id,
        trigger=trigger,
        origin=InvestigationOrigin.CHAT,
    )

    investigation_id = record.id

    # Post the acknowledgment and get the message ts for stage updates
    ack = DetachedInvestigationAck(
        investigation_id=investigation_id,
        message=f"Investigation {investigation_id[:8]} queued. I'll post results here when complete.",
    )
    notifier.post_ack(delivery_target, ack)

    # Start processing: if we have a database, rely on worker; otherwise run in-process
    if database_dsn():
        # Worker will pick it up
        _ensure_chat_worker_started(store)
        logger.info("Chat investigation %s queued for worker", investigation_id)
    else:
        # Run in-process fallback on a background thread.
        #
        # A new thread starts with an empty context, so the storage scope this turn is
        # bound to would not reach the run and it would resolve integrations unbound.
        # Take the copy here, on the submitting thread, and one per thread: a Context is
        # single-entry, and copying inside the worker would snapshot the wrong thread.
        logger.info("Chat investigation %s running in-process (no database)", investigation_id)
        ctx = copy_context()
        thread = threading.Thread(
            target=ctx.run,
            args=(_run_investigation_background, store, investigation_id, delivery_target),
            name=f"chat-investigation-{investigation_id[:8]}",
            daemon=True,
        )
        thread.start()

    # Record that this turn accepted a detached investigation, and which call
    # id triggered it — ``current_tool_call_id()`` is unbound (None) outside a
    # tool invocation (e.g. a REPL launch with no chat turn), which is fine:
    # ``note_accepted`` treats a blank call id as "unknown", not "this call".
    launch_record = current_detached_launch_record()
    if launch_record is not None:
        launch_record.note_accepted(investigation_id, call_id=current_tool_call_id())

    return DetachedLaunchResult(
        investigation_id=investigation_id,
        accepted=True,
    )


def _run_investigation_background(
    store: InvestigationStore,
    investigation_id: str,
    delivery_target: ChatDeliveryTarget,
) -> None:
    """Run investigation in background and deliver result via chat notifier."""
    try:
        # Lazy import to avoid cycles
        from gateway.core.investigations.chat_worker import run_investigation_in_process

        # run_investigation_in_process handles delivery itself and returns None
        run_investigation_in_process(store, investigation_id)
        logger.info("Chat investigation %s completed in background", investigation_id)

    except Exception:
        logger.exception("Chat investigation %s background run failed", investigation_id)

        # Try to deliver failure notification
        registry = get_chat_notifier_registry()
        notifier = registry.get(delivery_target.platform)
        if notifier:
            error_summary = "Investigation failed unexpectedly"
            notifier.report_failure(delivery_target, error_summary, investigation_id)


def _get_store() -> InvestigationStore:
    """Get investigation store (shared utility)."""
    from gateway.core.investigations.storage_utils import get_investigation_store

    return get_investigation_store()


# Process-global chat worker instance (similar pattern to existing worker)
_chat_worker_lock = threading.Lock()
_chat_worker: Any | None = None


def _ensure_chat_worker_started(store: InvestigationStore) -> Any | None:
    """Start the process-wide chat worker on first call."""
    global _chat_worker
    with _chat_worker_lock:
        if _chat_worker is None:
            # Lazy import to avoid cycles
            from gateway.core.investigations.chat_worker import ChatInvestigationWorker

            _chat_worker = ChatInvestigationWorker(store)
            _chat_worker.start()
            logger.info("[chat-investigations] worker started")
        return _chat_worker


class GatewayDetachedLauncher:
    """Gateway implementation of detached investigation launcher."""

    def launch(
        self,
        *,
        alert_text: str,
        context_overrides: dict[str, Any] | None = None,
    ) -> DetachedLaunchResult:
        """Launch a detached investigation via the gateway system."""
        return launch_detached_investigation(
            alert_text=alert_text,
            context_overrides=context_overrides,
        )


@contextmanager
def bind_gateway_detached_launcher(record: DetachedLaunchRecord) -> Iterator[None]:
    """Bind the gateway detached launcher and this turn's launch record."""
    launcher = GatewayDetachedLauncher()
    with bound_detached_launcher(launcher), bound_detached_launch_record(record):
        yield
