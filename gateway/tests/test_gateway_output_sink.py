from __future__ import annotations

from unittest.mock import MagicMock

from gateway.agent.gateway_output_sink import GatewayOutputSink
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
