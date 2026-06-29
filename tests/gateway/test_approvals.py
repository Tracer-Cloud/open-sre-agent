from __future__ import annotations

import threading
import time

import pytest

from gateway.approvals.store import ApprovalStore
from gateway.approvals.telegram import TelegramApprovalService
from gateway.config import GatewaySettings
from gateway.db import connect_gateway_db


class _FakeClient:
    def send_message(self, chat_id: str, text: str, *, reply_markup=None) -> tuple[bool, str, str]:
        _ = (chat_id, text, reply_markup)
        return True, "", "10"

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        _ = (callback_query_id, text)


@pytest.fixture
def approval_service(tmp_path) -> TelegramApprovalService:
    conn = connect_gateway_db(tmp_path / "state.db")
    store = ApprovalStore(conn)
    settings = GatewaySettings(approval_timeout_seconds=2, gate_side_effects=True)
    service = TelegramApprovalService(client=_FakeClient(), store=store, settings=settings)
    yield service
    conn.close()


def test_callback_approves_waiter(approval_service: TelegramApprovalService) -> None:
    result: list[str] = []

    def worker() -> None:
        result.append(approval_service.wait_for_confirmation(chat_id="42", prompt="Proceed?"))

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.1)
    with approval_service._lock:
        approval_id = next(iter(approval_service._waiters))
    approval_service.handle_callback(
        user_id="42",
        callback_data=f"approve:{approval_id}",
        callback_query_id="cq",
    )
    thread.join(timeout=2)
    assert result == ["yes"]


@pytest.mark.xfail(
    strict=True,
    reason="bug: handle_callback calls store.resolve() (UPDATE+commit) before verifying "
    "the caller owns the approval, so a non-owner can flip another chat's pending status",
)
def test_callback_from_non_owner_leaves_approval_pending(
    approval_service: TelegramApprovalService,
) -> None:
    approval_id = approval_service._store.create(
        chat_id="42",
        message_id="pending",
        tool_name="dangerous_tool",
        payload_hash="hash",
        expires_at=time.time() + 60,
    )
    approval_service.handle_callback(
        user_id="99",  # not the owner ("42")
        callback_data=f"approve:{approval_id}",
        callback_query_id="cq",
    )
    row = approval_service._store.get(approval_id)
    assert row is not None
    assert row["status"] == "pending"
