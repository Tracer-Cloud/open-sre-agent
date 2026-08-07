from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from gateway.core.runtime.approvals import ApprovalBroker
from gateway.transports.buzz import background
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.runtime import BuzzPollingRuntime
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings


def _resources() -> BuzzPollingRuntime:
    return BuzzPollingRuntime(
        client=MagicMock(),
        bindings=MagicMock(),
        session_resolver=MagicMock(),
        chat_locks={},
        executor=MagicMock(),
    )


def _reply_event(
    *, pubkey: str, target_event_id: str, content: str = "approve"
) -> BuzzInboundMessage:
    return BuzzInboundMessage(
        event_id="reply1",
        pubkey=pubkey,
        channel_id="chan-1",
        content=content,
        created_at=100,
        reply_event_ids=frozenset({target_event_id}),
    )


def test_unauthorized_reply_does_not_resolve_the_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unauthorized participant must not approve/deny by replying to the prompt."""
    resources = _resources()
    resources.pending_approvals.register("prompt1", "approval-id-1")
    settings = GatewaySettings(private_key="k", allowed_pubkeys=["a" * 64])
    monkeypatch.setattr(background, "is_pubkey_authorized", lambda **_kw: False)

    resolved = background._resolve_if_approval_reply(
        _reply_event(pubkey="f" * 64, target_event_id="prompt1"), resources, settings
    )

    assert resolved is False
    # Not consumed — the real, authorized responder can still resolve it later.
    assert resources.pending_approvals.peek_match(frozenset({"prompt1"})) == "approval-id-1"


def test_authorized_reply_resolves_the_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _resources()
    resources.pending_approvals.register("prompt1", "approval-id-1")
    settings = GatewaySettings(private_key="k", allowed_pubkeys=["a" * 64])
    monkeypatch.setattr(background, "is_pubkey_authorized", lambda **_kw: True)
    resolve = MagicMock()
    monkeypatch.setattr(resources.approvals, "resolve", resolve)

    resolved = background._resolve_if_approval_reply(
        _reply_event(pubkey="a" * 64, target_event_id="prompt1"), resources, settings
    )

    assert resolved is True
    resolve.assert_called_once_with("approval-id-1", approved=True, decided_by="a" * 64)
    assert resources.pending_approvals.peek_match(frozenset({"prompt1"})) is None


def test_poll_loop_does_not_block_on_a_slow_turn() -> None:
    """The poll loop must keep polling while a turn is in flight (approval-wait fix)."""
    turn_started = asyncio.Event()
    release_turn = asyncio.Event()

    async def _slow_dispatch(_event: object, **_kw: object) -> None:
        turn_started.set()
        await release_turn.wait()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            background._dispatch_turn(  # type: ignore[arg-type]
                MagicMock(pubkey="p", channel_id="c"),
                client=MagicMock(),
                session_resolver=MagicMock(),
                settings=MagicMock(),
                executor=MagicMock(),
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(4),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                loop=loop,
                handle_callback_to_gateway_agent=MagicMock(),
                logger=MagicMock(),
            )
        )
        await asyncio.wait_for(turn_started.wait(), timeout=1)
        # The turn is blocked, but this coroutine (standing in for the poll
        # loop) was never awaited on the task itself, so it can keep going —
        # exactly what `asyncio.create_task` (not `await`) in the real loop buys.
        assert not task.done()
        release_turn.set()
        await task

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            background,
            "handle_polled_inbound_buzz_message",
            _slow_dispatch,
        )
        asyncio.run(_run())
