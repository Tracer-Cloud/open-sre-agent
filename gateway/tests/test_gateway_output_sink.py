from __future__ import annotations

import logging
from unittest.mock import MagicMock

from gateway.gateway_output_sink import GatewayOutputSink
from gateway.polling.telegram_poller.client import TelegramBotClient
from platform.notifications.limits import MAX_MESSAGE_SIZE


def test_initial_status_is_not_working_placeholder() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")

    GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    sent = client.send_message.call_args.args[1]
    assert sent != "Working…"
    assert sent.endswith("…")
    client.send_chat_action.assert_called_with("123", "typing")


def test_set_status_never_shows_working_placeholder() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.set_tool_status("Working…")

    edited = client.edit_message_text.call_args.args[2]
    assert edited != "Working…"
    assert not edited.startswith("Working")


def test_render_response_header_uses_friendly_assistant_status() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.render_response_header("assistant")

    edited = client.edit_message_text.call_args.args[2]
    assert edited == "💬 Composing your reply…"


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


def test_finalize_renders_headers_and_tables_as_html() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.finalize("## Open PRs\n\n| # | Title |\n|---|---|\n| 3811 | docs fix |\n\n---\n\n**Done**")

    html_text = client.edit_message_text.call_args.args[2]
    assert "<b>Open PRs</b>" in html_text
    assert "• <b>3811</b> — docs fix" in html_text
    assert "<b>Done</b>" in html_text
    assert "|---|" not in html_text


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


def test_render_error_appends_auth_login_hint_on_credit_exhaustion() -> None:
    from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER

    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.render_error(f"Anthropic {CREDIT_EXHAUSTED_MARKER}. Original error: 400")

    finalized = client.edit_message_text.call_args[0][2]
    assert "opensre auth login" in finalized


def test_render_error_no_auth_hint_for_generic_error() -> None:
    client = MagicMock(spec=TelegramBotClient)
    client.send_message.return_value = (True, "", "1")
    client.edit_message_text.return_value = (True, "")
    sink = GatewayOutputSink(client=client, chat_id="123", edit_interval_seconds=0.0)

    sink.render_error("something else broke")

    finalized = client.edit_message_text.call_args[0][2]
    assert "opensre auth login" not in finalized
