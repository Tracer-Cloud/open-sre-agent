from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.config import load_gateway_settings


@pytest.mark.xfail(
    strict=True,
    reason="bug: int(port_raw) raises ValueError on a blank TELEGRAM_WEBHOOK_PORT "
    "instead of falling back to the default port",
)
def test_blank_webhook_port_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_PORT", "")
    with patch("gateway.config.get_integration", return_value=None):
        settings = load_gateway_settings()
    assert settings.webhook_port == 8443
