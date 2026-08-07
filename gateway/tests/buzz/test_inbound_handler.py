from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from gateway.core.runtime.approvals import ApprovalBroker
from gateway.transports.buzz import inbound_security
from gateway.transports.buzz.inbound_handler import handle_polled_inbound_buzz_message
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings
from integrations.buzz.client import BuzzClient


@pytest.fixture(autouse=True)
def _no_integration_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the identity-policy lookup off the developer's real integration store."""
    monkeypatch.setattr(inbound_security, "get_integration", lambda *_a, **_kw: None)


def _event(pubkey: str = "a" * 64) -> BuzzInboundMessage:
    return BuzzInboundMessage(
        event_id="ev1",
        pubkey=pubkey,
        channel_id="chan-1",
        content="@bot hello",
        created_at=100,
        reply_event_ids=frozenset(),
    )


def _settings(*, allowed: list[str]) -> GatewaySettings:
    return GatewaySettings(private_key="k", allowed_pubkeys=allowed)


def _run(coro: object) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


def test_authorized_sender_invokes_the_callback() -> None:
    client = MagicMock(spec=BuzzClient)
    client.send_message.return_value = {"success": True, "error": "", "event_id": "status1"}
    session_resolver = MagicMock()
    session_resolver.resolve.return_value = MagicMock(session_id="sess-1")
    callback = MagicMock()
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        _run(
            handle_polled_inbound_buzz_message(
                _event(),
                client=client,
                session_resolver=session_resolver,
                settings=_settings(allowed=["a" * 64]),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(4),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                handle_callback_to_gateway_agent=callback,
            )
        )
    finally:
        executor.shutdown(wait=True)

    callback.assert_called_once()
    assert callback.call_args.args[0] == "@bot hello"


def test_unauthorized_sender_never_invokes_the_callback() -> None:
    client = MagicMock(spec=BuzzClient)
    session_resolver = MagicMock()
    callback = MagicMock()
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        _run(
            handle_polled_inbound_buzz_message(
                _event(pubkey="f" * 64),
                client=client,
                session_resolver=session_resolver,
                settings=_settings(allowed=["a" * 64]),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(4),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                handle_callback_to_gateway_agent=callback,
            )
        )
    finally:
        executor.shutdown(wait=True)

    callback.assert_not_called()
    session_resolver.resolve.assert_not_called()
