from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gateway.app import app
from gateway.config import GatewaySettings


@pytest.fixture
def client() -> TestClient:
    settings = GatewaySettings(bot_token="test:token", webhook_secret="secret")
    runner = MagicMock()
    runner.setup_webhook.return_value = (True, "")
    runner.handle_inbound = AsyncMock()
    with (
        patch("gateway.app.GatewayRunner", return_value=runner),
        patch("gateway.app.load_gateway_settings", return_value=settings),
        patch("gateway.app.set_runner"),
        TestClient(app) as test_client,
    ):
        test_client.app.state.runner = runner
        yield test_client


def test_webhook_rejects_bad_secret(client: TestClient) -> None:
    response = client.post("/telegram/webhook", json={"update_id": 1})
    assert response.status_code == 403


def test_webhook_accepts_update(client: TestClient) -> None:
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 42},
            "chat": {"id": 42, "type": "private"},
            "text": "hello",
        },
    }
    response = client.post(
        "/telegram/webhook",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
