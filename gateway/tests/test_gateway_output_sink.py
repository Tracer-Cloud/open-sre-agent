from __future__ import annotations

import logging
from unittest.mock import MagicMock

from config.gateway_output_sink import GatewayOutputSink
from gateway.polling.telegram_poller.client import TelegramBotClient


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
    assert len(edited) <= 4096


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
