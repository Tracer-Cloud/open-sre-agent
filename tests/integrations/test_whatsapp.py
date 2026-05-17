"""Tests for WhatsApp integration config, catalog, and verification."""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations._verification_adapters import _verify_whatsapp
from app.integrations.config_models import WhatsAppConfig


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


def test_whatsapp_config_validates_required_fields() -> None:
    config = WhatsAppConfig(
        phone_number_id="123456789",
        access_token="EAAB...",
        default_to="+1234567890",
    )

    assert config.phone_number_id == "123456789"
    assert config.access_token == "EAAB..."
    assert config.default_to == "+1234567890"


def test_whatsapp_config_rejects_empty_phone_number_id() -> None:
    with pytest.raises(ValueError, match="phone_number_id"):
        WhatsAppConfig(phone_number_id="   ", access_token="tok")


def test_whatsapp_config_rejects_empty_access_token() -> None:
    with pytest.raises(ValueError, match="access_token"):
        WhatsAppConfig(phone_number_id="123", access_token="  ")


def test_whatsapp_config_default_to_optional() -> None:
    config = WhatsAppConfig(phone_number_id="123", access_token="tok")

    assert config.default_to is None


def test_verify_whatsapp_missing_phone_number_id() -> None:
    result = _verify_whatsapp("env", {"access_token": "tok"})

    assert result["status"] == "missing"
    assert "phone_number_id" in result["detail"].lower()


def test_verify_whatsapp_missing_access_token() -> None:
    result = _verify_whatsapp("env", {"phone_number_id": "123"})

    assert result["status"] == "missing"
    assert "access_token" in result["detail"].lower()


def test_verify_whatsapp_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_get(*args: Any, **kwargs: Any) -> Any:
        return _FakeResponse({"id": "123", "display_phone_number": "+1 555 123 4567"})

    monkeypatch.setattr("app.integrations._verification_adapters.requests.get", _fake_get)

    result = _verify_whatsapp("env", {"phone_number_id": "123", "access_token": "tok"})

    assert result["status"] == "passed"
    assert "+1 555 123 4567" in result["detail"]


def test_verify_whatsapp_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_get(*args: Any, **kwargs: Any) -> Any:
        raise Exception("Connection timeout")

    monkeypatch.setattr("app.integrations._verification_adapters.requests.get", _fake_get)

    result = _verify_whatsapp("env", {"phone_number_id": "123", "access_token": "tok"})

    assert result["status"] == "failed"
    assert "Connection timeout" in result["detail"]


def test_verify_whatsapp_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_get(*args: Any, **kwargs: Any) -> Any:
        return _FakeResponse({}, status_code=401)

    monkeypatch.setattr("app.integrations._verification_adapters.requests.get", _fake_get)

    result = _verify_whatsapp("env", {"phone_number_id": "123", "access_token": "tok"})

    assert result["status"] == "failed"


def test_catalog_resolve_whatsapp_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_DEFAULT_TO", "+1234567890")

    from app.integrations.catalog import resolve_effective_integrations

    effective = resolve_effective_integrations()

    assert "whatsapp" in effective
    assert effective["whatsapp"]["source"] == "local env"
    assert effective["whatsapp"]["config"]["phone_number_id"] == "123"
    assert effective["whatsapp"]["config"]["access_token"] == "tok"
    assert effective["whatsapp"]["config"]["default_to"] == "+1234567890"


def test_catalog_skips_whatsapp_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)

    from app.integrations.catalog import resolve_effective_integrations

    effective = resolve_effective_integrations()

    assert "whatsapp" not in effective
