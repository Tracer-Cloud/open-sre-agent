from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock, patch

from gateway.transports.telegram.poller.client import TelegramBotClient
from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse


@patch("gateway.transports.telegram.poller.client.post_json")
def test_send_message_success(mock_post: MagicMock) -> None:
    mock_post.return_value = MagicMock(
        ok=True,
        status_code=200,
        data={"ok": True, "result": {"message_id": 99}},
    )
    client = TelegramBotClient("token")
    ok, error, message_id = client.send_message("123", "hello")
    assert ok is True
    assert error == ""
    assert message_id == "99"


@patch("gateway.transports.telegram.poller.client.post_json")
def test_send_message_success_with_mapping_proxy_data(mock_post: MagicMock) -> None:
    mock_post.return_value = DeliveryResponse(
        ok=True,
        status_code=200,
        data=MappingProxyType({"ok": True, "result": {"message_id": 42}}),
    )
    client = TelegramBotClient("token")
    ok, error, message_id = client.send_message("123", "hello")
    assert ok is True
    assert error == ""
    assert message_id == "42"


@patch("gateway.transports.telegram.poller.client.post_json")
def test_send_message_redacts_bot_token_from_transport_exception(
    mock_post: MagicMock, caplog: object
) -> None:
    import logging

    caplog.set_level(logging.WARNING, logger="gateway.transports.telegram.poller.client")
    mock_post.return_value = DeliveryResponse(
        ok=False,
        error="Connection refused for url: https://api.telegram.org/botSECRET/sendMessage",
        exc_type="ConnectError",
    )
    client = TelegramBotClient("SECRET")
    ok, error, message_id = client.send_message("123", "hello")
    assert ok is False
    assert message_id == ""
    assert "/botSECRET/" not in error
    assert "SECRET" not in error
    assert "ConnectError" in error
    log_messages = [record.message for record in caplog.records]
    assert not any("/botSECRET/" in message or "SECRET" in message for message in log_messages)
