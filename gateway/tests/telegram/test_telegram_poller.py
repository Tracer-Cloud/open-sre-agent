from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from gateway.transports.telegram.poller.poller import TelegramPoller, _decode_telegram_response


def test_decode_telegram_response_parses_non_200_json() -> None:
    response = httpx.Response(
        409,
        json={
            "ok": False,
            "error_code": 409,
            "description": "Conflict: terminated by other getUpdates request",
        },
    )
    data = _decode_telegram_response(response)
    assert data["ok"] is False
    assert data["error_code"] == 409


@patch("gateway.transports.telegram.poller.poller.time.sleep")
@patch("gateway.transports.telegram.poller.poller.httpx.get")
def test_poll_once_conflict_is_debug_not_warning(
    mock_get: MagicMock,
    mock_sleep: MagicMock,
    caplog: object,
) -> None:
    import logging

    caplog.set_level(logging.DEBUG, logger="gateway.transports.telegram.poller.poller")
    mock_get.return_value = httpx.Response(
        409,
        json={
            "ok": False,
            "error_code": 409,
            "description": "Conflict: terminated by other getUpdates request",
        },
    )
    poller = TelegramPoller("tok")
    assert poller.poll_once() == []
    mock_sleep.assert_called_once_with(2.0)
    assert not any(
        "[telegram-gateway] getUpdates not ok" in record.message for record in caplog.records
    )


@patch("gateway.transports.telegram.poller.poller.time.sleep")
@patch("gateway.transports.telegram.poller.poller.httpx.get")
def test_poll_once_success_resets_conflict_backoff(
    mock_get: MagicMock, _mock_sleep: MagicMock
) -> None:
    mock_get.side_effect = [
        httpx.Response(
            409,
            json={"ok": False, "error_code": 409, "description": "conflict"},
        ),
        httpx.Response(200, json={"ok": True, "result": []}),
    ]
    poller = TelegramPoller("tok")
    poller._conflict_backoff_seconds = 8.0
    assert poller.poll_once() == []
    assert poller.poll_once() == []
    assert poller._conflict_backoff_seconds == 2.0


@patch("gateway.transports.telegram.poller.poller.time.sleep")
@patch("gateway.transports.telegram.poller.poller.httpx.get")
def test_poll_once_parses_inbound_message(mock_get: MagicMock, mock_sleep: MagicMock) -> None:
    mock_get.return_value = httpx.Response(
        200,
        json={
            "ok": True,
            "result": [
                {
                    "update_id": 7,
                    "message": {
                        "message_id": 11,
                        "from": {"id": 42},
                        "chat": {"id": 99, "type": "private"},
                        "text": "hello",
                    },
                }
            ],
        },
    )
    events = TelegramPoller("tok").poll_once()
    assert len(events) == 1
    assert events[0].text == "hello"
    assert events[0].chat_id == "99"
    mock_sleep.assert_not_called()


@patch("gateway.transports.telegram.poller.poller.time.sleep")
@patch("gateway.transports.telegram.poller.poller.httpx.get")
def test_poll_once_exception_omits_bot_token_detail(
    mock_get: MagicMock,
    _mock_sleep: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    bot_token = "secret_bot_token_12345"
    mock_get.side_effect = httpx.ConnectError(
        f"failed to connect to https://api.telegram.org/bot{bot_token}/getUpdates"
    )
    poller = TelegramPoller(bot_token)
    with caplog.at_level(logging.WARNING, logger="gateway.transports.telegram.poller.poller"):
        assert poller.poll_once() == []

    assert any("[telegram-gateway] getUpdates failed: ConnectError" in r.message for r in caplog.records)
    for record in caplog.records:
        assert bot_token not in record.message

