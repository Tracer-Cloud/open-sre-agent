"""Every transport must release its approval slot when the prompt never posts.

``ApprovalBroker.wait`` is the only path that removes a pending entry, so a
prompter that gives up before waiting — the post to the chat failed, so there is
no button to click — leaves the request pending for the life of the process.
``close`` would then deny it at shutdown and write an ``approval.resolve``
record for a decision nobody was ever asked to make.

One file for all four transports on purpose: the bug was identical in each, and
a per-transport test would not have caught the next transport repeating it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.buzz.approvals import BuzzApprovalPrompter
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.discord import approvals as discord_approvals
from gateway.transports.discord.approvals import DiscordApprovalPrompter
from gateway.transports.slack.delivery.approvals import ThreadApprovalPrompter
from gateway.transports.telegram.approvals import TelegramApprovalPrompter

REQUESTER = "a" * 64


def _telegram(broker: ApprovalBroker, _monkeypatch: pytest.MonkeyPatch) -> Any:
    client = MagicMock()
    client.send_message.return_value = (False, "post failed", "")
    return TelegramApprovalPrompter(client=client, broker=broker, chat_id="chat-1")


def _slack(broker: ApprovalBroker, _monkeypatch: pytest.MonkeyPatch) -> Any:
    client = MagicMock()
    client.post_message.return_value = None
    return ThreadApprovalPrompter(
        client=client, broker=broker, channel_id="C1", thread_ts="123.456"
    )


def _discord(broker: ApprovalBroker, monkeypatch: pytest.MonkeyPatch) -> Any:
    def _post_fails(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(discord_approvals, "send_message_with_components", _post_fails)
    return DiscordApprovalPrompter(broker=broker, bot_token="tok", channel_id="C1")


def _buzz(broker: ApprovalBroker, _monkeypatch: pytest.MonkeyPatch) -> Any:
    client = MagicMock()
    client.send_message.return_value = {"success": False, "error": "post failed"}
    return BuzzApprovalPrompter(
        broker=broker,
        client=client,
        channel_id="chan-1",
        requester_pubkey=REQUESTER,
        pending_approvals=PendingApprovals(),
    )


@pytest.mark.parametrize(
    "build_prompter",
    [
        pytest.param(_telegram, id="telegram"),
        pytest.param(_slack, id="slack"),
        pytest.param(_discord, id="discord"),
        pytest.param(_buzz, id="buzz"),
    ],
)
def test_failed_prompt_post_leaves_no_pending_approval(
    build_prompter: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    broker = ApprovalBroker()
    prompter = build_prompter(broker, monkeypatch)

    # Act — the prompt cannot be posted, so nobody can ever decide it.
    approved, decided_by = prompter.request(
        tool_name="send_message",
        reason="Sends a message on your behalf.",
        arguments={"message": "hi"},
        expiry_seconds=1.0,
    )

    # Assert — fails closed, and holds nothing for shutdown to deny.
    assert (approved, decided_by) == (False, "")
    assert broker.close() == 0
