from __future__ import annotations

import logging
from unittest.mock import MagicMock

from config.gateway_output_sink import GatewayOutputSink
from gateway.polling.telegram_poller.client import TelegramBotClient
from platform.notifications.limits import MAX_MESSAGE_SIZE


def test_stream_throttles_edits() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=10.0)
    text = sink.stream(label="assistant", chunks=["hello", " world"])
    assert text == "hello world"
    assert client.edit_message_text.call_count >= 1


def test_finalize_truncates_long_text() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)
    sink.finalize("x" * 5000)
    edited = client.edit_message_text.call_args[0][2]
    assert len(edited) <= MAX_MESSAGE_SIZE


def test_finalize_logs_outbound_edited_message(caplog) -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    with caplog.at_level(logging.INFO, logger="gateway"):
        sink.finalize("hello\nteam")

    assert "outbound chat=123 text='hello team'" in caplog.text


def test_finalize_logs_outbound_fallback_send(caplog) -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.side_effect = [(True, "", "1"), (True, "", "2")]
    client.edit_message_text.return_value = (False, "edit failed")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    with caplog.at_level(logging.INFO, logger="gateway"):
        sink.finalize("fallback message")

    assert "outbound chat=123 text='fallback message'" in caplog.text


def test_finalize_renders_markdown_as_html() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.finalize("**bold** and `code`")

    call = client.edit_message_text.call_args
    assert call.kwargs.get("parse_mode") == "HTML"
    assert "<b>bold</b>" in call.args[2]
    assert "<code>code</code>" in call.args[2]


def test_finalize_falls_back_to_plain_when_html_rejected() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    # HTML edit fails (bad markup); plain retry succeeds.
    client.edit_message_text.side_effect = [(False, "can't parse entities"), (True, "")]
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.finalize("**bold**")

    assert client.edit_message_text.call_count == 2
    first, second = client.edit_message_text.call_args_list
    assert first.kwargs.get("parse_mode") == "HTML"
    assert second.kwargs.get("parse_mode", "") == ""
    assert second.args[2] == "**bold**"
