from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock, patch

from gateway.polling.telegram_poller.client import TelegramBotClient
from platform.notifications.delivery_transport import DeliveryResponse


@patch("gateway.polling.telegram_poller.client.post_json")
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


@patch("gateway.polling.telegram_poller.client.post_json")
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
