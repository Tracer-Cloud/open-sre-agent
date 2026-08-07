"""Handlers for polled inbound Buzz mentions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from gateway.core.runtime.approvals import ApprovalBroker, approval_tool_hooks
from gateway.core.runtime.sink_protocol import GatewayAgentCallback
from gateway.core.storage import SessionResolver
from gateway.transports.buzz.approvals import BuzzApprovalPrompter
from gateway.transports.buzz.inbound_security import enforce_inbound_buzz_message_security
from gateway.transports.buzz.output_sink import BuzzOutputSink
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.session_rotation import conversation_key, resolve_or_rotate_session
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings
from integrations.buzz.client import BuzzClient
from platform.analytics.usage_context import SURFACE_BUZZ, bound_usage_context

logger = logging.getLogger(__name__)


async def handle_polled_inbound_buzz_message(
    event: BuzzInboundMessage,
    *,
    client: BuzzClient,
    session_resolver: SessionResolver,
    settings: GatewaySettings,
    executor: ThreadPoolExecutor,
    chat_locks: dict[str, asyncio.Lock],
    turn_semaphore: asyncio.Semaphore,
    approvals: ApprovalBroker,
    pending_approvals: PendingApprovals,
    loop: asyncio.AbstractEventLoop | None = None,
    handle_callback_to_gateway_agent: GatewayAgentCallback,
    on_handled: Callable[[], None] | None = None,
) -> None:
    """Process one long-polled inbound Buzz mention/reply.

    *on_handled* fires on the executor thread the moment the turn body
    returns, not when the awaiting coroutine resumes. Shutdown cancels the
    task awaiting :func:`run_in_executor`, which does not stop the thread
    underneath it — so a turn can run to completion, post its answer and
    apply its tool side effects while the cancelled `await` never comes back.
    Acknowledging from inside the executor keeps that finished work off the
    replay path; re-running it would duplicate those side effects.
    """
    key = conversation_key(event)
    user_lock = chat_locks.setdefault(key, asyncio.Lock())
    decision = enforce_inbound_buzz_message_security(
        pubkey=event.pubkey,
        channel_id=event.channel_id,
        text=event.content,
        env_allowed_pubkeys=settings.allowed_pubkeys,
    )
    async with user_lock, turn_semaphore:
        session = resolve_or_rotate_session(
            event,
            decision,
            session_resolver=session_resolver,
            client=client,
        )
        if session is None:
            return

        preview = event.content.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = f"{preview[:77]}..."
        logger.info(
            "inbound pubkey=%s channel=%s session=%s text=%r",
            event.pubkey,
            event.channel_id,
            session.session_id[:8],
            preview,
        )

        sink = BuzzOutputSink(
            client=client,
            channel_id=event.channel_id,
            edit_interval_seconds=settings.stream_edit_interval_seconds,
            tool_hooks=approval_tool_hooks(
                BuzzApprovalPrompter(
                    broker=approvals,
                    client=client,
                    channel_id=event.channel_id,
                    requester_pubkey=event.pubkey,
                    pending_approvals=pending_approvals,
                )
            ),
        )

        event_loop = loop or asyncio.get_running_loop()

        def _run_turn() -> None:
            try:
                with bound_usage_context(
                    surface=SURFACE_BUZZ,
                    session_id=session.session_id,
                    user_id=event.pubkey or None,
                ):
                    handle_callback_to_gateway_agent(
                        event.content,
                        session,
                        sink,
                        logger,
                    )
            finally:
                if on_handled is not None:
                    on_handled()

        await event_loop.run_in_executor(executor, _run_turn)


__all__ = ["handle_polled_inbound_buzz_message"]
