"""Discord approval prompt: the tool's own wording, and the receipt after it runs."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from gateway.core.runtime.approvals import ApprovalBroker
from gateway.transports.discord import approvals as discord_approvals
from gateway.transports.discord.approvals import DiscordApprovalPrompter


class _FakeDiscord:
    """Records what the prompter would post to and edit in the channel."""

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []

    def send_message_with_components(
        self,
        *,
        channel_id: str,
        content: str,
        components: Any,
        **_rest: Any,
    ) -> str | None:
        self.posts.append({"channel_id": channel_id, "content": content, "components": components})
        return f"msg-{len(self.posts)}"

    def edit_message(
        self,
        *,
        channel_id: str,
        message_id: str,
        content: str,
        **_rest: Any,
    ) -> bool:
        self.edits.append({"channel_id": channel_id, "message_id": message_id, "content": content})
        return True


@pytest.fixture
def discord_api(monkeypatch: pytest.MonkeyPatch) -> _FakeDiscord:
    fake = _FakeDiscord()
    monkeypatch.setattr(
        discord_approvals, "send_message_with_components", fake.send_message_with_components
    )
    monkeypatch.setattr(discord_approvals, "edit_message", fake.edit_message)
    return fake


def _prompter(broker: ApprovalBroker) -> DiscordApprovalPrompter:
    return DiscordApprovalPrompter(broker=broker, bot_token="t", channel_id="C1")


def _approve_later(broker: ApprovalBroker, fake: _FakeDiscord) -> None:
    """Simulate the authorized user clicking Approve once the buttons are up."""

    def _click() -> None:
        custom_id = str(fake.posts[-1]["components"][0]["components"][0]["custom_id"])
        broker.resolve(custom_id.split(":", 1)[1], approved=True, decided_by="111")

    threading.Timer(0.05, _click).start()


def test_prompt_carries_the_headline_and_details(discord_api: _FakeDiscord) -> None:
    broker = ApprovalBroker()
    _approve_later(broker, discord_api)

    approved, decided_by = _prompter(broker).request(
        call_id="tc-1",
        headline="Create PR — drop the orphan",
        reason="Opens a pull request on GitHub.",
        details="acme/platform\nmain  ←  fix/drop-orphan",
        expiry_seconds=5,
    )

    assert (approved, decided_by) == (True, "111")
    body = discord_api.posts[-1]["content"]
    assert "**Approval needed — Create PR — drop the orphan**" in body
    # Details are fenced so model-supplied text cannot render as markdown.
    assert "```\nacme/platform\nmain  ←  fix/drop-orphan\n```" in body
    assert discord_api.edits[-1]["content"] == (
        "✅ Create PR — drop the orphan — approved by <@111>"
    )


def test_receipt_rewrites_the_outcome_of_that_prompt(discord_api: _FakeDiscord) -> None:
    """The PR number only exists after the call, so it lands in a second edit."""
    broker = ApprovalBroker()
    prompter = _prompter(broker)
    _approve_later(broker, discord_api)
    prompter.request(
        call_id="tc-1",
        headline="Create PR — drop the orphan",
        reason="",
        details="",
        expiry_seconds=5,
    )

    prompter.attach_receipt(call_id="tc-1", receipt="Create PR #7 — drop the orphan  https://x/7")

    assert discord_api.edits[-1]["message_id"] == discord_api.edits[-2]["message_id"]
    assert discord_api.edits[-1]["content"] == (
        "✅ Create PR #7 — drop the orphan  https://x/7 — approved by <@111>"
    )


def test_an_unknown_call_has_nothing_to_rewrite(discord_api: _FakeDiscord) -> None:
    """A denied or expired prompt is never remembered, so no edit may fire."""
    _prompter(ApprovalBroker()).attach_receipt(call_id="tc-1", receipt="Create PR #7")

    assert not discord_api.edits


def test_a_receipt_is_only_applied_once(discord_api: _FakeDiscord) -> None:
    broker = ApprovalBroker()
    prompter = _prompter(broker)
    _approve_later(broker, discord_api)
    prompter.request(call_id="tc-1", headline="Create PR", reason="", details="", expiry_seconds=5)
    prompter.attach_receipt(call_id="tc-1", receipt="Create PR #7")
    after_first = len(discord_api.edits)

    prompter.attach_receipt(call_id="tc-1", receipt="Create PR #7")

    assert len(discord_api.edits) == after_first


def test_a_failed_prompt_post_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discord_approvals, "send_message_with_components", lambda **_kw: None)

    approved, decided_by = _prompter(ApprovalBroker()).request(
        call_id="tc-1", headline="Create PR", reason="", details="", expiry_seconds=5
    )

    assert (approved, decided_by) == (False, "")
