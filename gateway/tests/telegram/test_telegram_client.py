from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock, patch

from gateway.transports.telegram.poller.client import TelegramBotClient
from platform.notifications.delivery_transport import DeliveryResponse


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
def test_client_redacts_token_from_transport_error(mock_post: MagicMock) -> None:
    token = "123456:SECRET"

    mock_post.return_value = DeliveryResponse(
        ok=False,
        error=(f"ConnectError: https://api.telegram.org/bot{token}/sendMessage"),
        exc_type="ConnectError",
    )

    client = TelegramBotClient(token)

    ok, error, message_id = client.send_message("123", "hello")

    assert ok is False
    assert message_id == ""
    assert token not in error
    assert f"/bot{token}/" not in error
    assert "<redacted>" in error
