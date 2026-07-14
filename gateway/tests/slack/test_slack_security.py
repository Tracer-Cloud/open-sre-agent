from __future__ import annotations

from gateway.slack.security import authorize_slack_message


def test_allowlisted_user_is_authorized() -> None:
    result = authorize_slack_message(
        user_id="U111",
        channel_id="C222",
        text="check disk usage",
        allowed_user_ids=["U111", "U999"],
    )
    assert bool(result) is True


def test_unlisted_user_is_denied_with_reason() -> None:
    result = authorize_slack_message(
        user_id="U666",
        channel_id="C222",
        text="check disk usage",
        allowed_user_ids=["U111"],
    )
    assert bool(result) is False
    assert "U666" in result.reason


def test_empty_allowlist_denied_without_open_flag() -> None:
    result = authorize_slack_message(
        user_id="Uanybody",
        channel_id="C222",
        text="hello",
        allowed_user_ids=[],
        allow_open_workspace=False,
    )
    assert bool(result) is False
    assert "allowlist" in result.reason.lower() or "SLACK_ALLOWED" in result.reason


def test_empty_allowlist_open_when_explicitly_enabled() -> None:
    result = authorize_slack_message(
        user_id="Uanybody",
        channel_id="C222",
        text="hello",
        allowed_user_ids=[],
        allow_open_workspace=True,
    )
    assert bool(result) is True
