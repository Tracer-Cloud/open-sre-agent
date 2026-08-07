"""Slack send works on a bot-token install, not only a webhook one.

The scheduler's ``_deliver_slack`` already posts via ``chat.postMessage`` when
it has a bot token, but this tool was webhook-only. On a token-only install the
prompt still told the model to call it, so the turn burned a tool call on
``slack_send_message is not available`` and could not post at all.
"""

from __future__ import annotations

from typing import Any

from integrations.slack.tools.slack_send_message_tool.tool import SlackSendMessageTool

_MESSAGE = "deploy finished — 3 services healthy"
_CHANNEL = "C0123ABCD"


def _tool() -> SlackSendMessageTool:
    return SlackSendMessageTool()


class TestAvailability:
    def test_available_with_a_webhook(self, monkeypatch) -> None:
        # Arrange
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

        # Act / Assert
        assert _tool().is_available({"slack": {"webhook_url": "https://hooks.slack.com/x"}}) is True

    def test_available_with_a_bot_token_and_no_webhook(self, monkeypatch) -> None:
        # Arrange: the shape of a real install — bot token, no webhook.
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

        # Act / Assert: absent here, the model is told to call a tool that is
        # not in the session and the turn wastes a call.
        assert _tool().is_available({"slack": {"bot_token": "xoxb-token"}}) is True

    def test_unavailable_with_neither(self, monkeypatch) -> None:
        # Arrange
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

        # Act / Assert
        assert _tool().is_available({"slack": {}}) is False


class TestBotTokenDelivery:
    def test_posts_to_the_named_channel_when_only_a_bot_token_exists(self, monkeypatch) -> None:
        # Arrange
        posted: dict[str, Any] = {}

        def _post(_target: Any, *, channel_id: str, text: str, thread_ts: str = "") -> tuple:
            posted["channel_id"] = channel_id
            posted["text"] = text
            return True, ""

        def _resolve() -> tuple[Any, str]:
            return object(), ""

        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(
            "integrations.slack.tools.slack_send_message_tool.tool.post_channel_message", _post
        )
        monkeypatch.setattr(
            "integrations.slack.tools.slack_send_message_tool.tool.resolve_bot_token", _resolve
        )

        # Act
        result = _tool().run(message=_MESSAGE, channel_id=_CHANNEL)

        # Assert
        assert result["sent"] is True
        assert posted["channel_id"] == _CHANNEL
        assert posted["text"] == _MESSAGE

    def test_bot_token_without_a_channel_is_a_configuration_error(self, monkeypatch) -> None:
        # Arrange: a bot token posts to a named channel, so with no channel and
        # no webhook there is nowhere to send.
        def _resolve() -> tuple[Any, str]:
            return object(), ""

        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(
            "integrations.slack.tools.slack_send_message_tool.tool.resolve_bot_token", _resolve
        )

        # Act
        result = _tool().run(message=_MESSAGE)

        # Assert: it must say so rather than silently sending nowhere.
        assert result["sent"] is False
        assert result["error_type"] == "configuration_error"


def test_channel_id_is_accepted_by_the_input_schema() -> None:
    # Arrange: additionalProperties is False, so a parameter run() accepts but
    # the schema omits would be rejected before it ever reaches the tool.
    schema = SlackSendMessageTool.input_schema

    # Act / Assert
    assert "channel_id" in schema["properties"]
    assert schema.get("additionalProperties") is False
