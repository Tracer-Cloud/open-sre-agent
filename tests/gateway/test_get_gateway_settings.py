from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from gateway.config.get_gateway_settings import (
    GatewayConfigurationError,
    GatewayEnv,
    GatewaySettings,
    choose_authorized_users,
    choose_bot_token,
    load_gateway_settings,
    load_telegram_credentials,
    store_allowed_users,
    store_bot_token,
)
from integrations.messaging_security import MessagingIdentityPolicy

_STORE_PATH = "gateway.config.get_gateway_settings.get_integration"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove all TELEGRAM_* env vars so GatewayEnv falls back to defaults.

    The root conftest loads a local ``.env`` into ``os.environ``; without this
    the gateway env settings would be non-deterministic across machines.
    """
    for key in list(os.environ):
        if key.startswith("TELEGRAM_"):
            monkeypatch.delenv(key, raising=False)
    yield


# ---------------------------------------------------------------------------
# GatewayEnv
# ---------------------------------------------------------------------------


def test_gateway_env_defaults(clean_env: None) -> None:
    env = GatewayEnv()
    assert env.bot_token == ""
    assert env.allowed_users == []
    assert env.webhook_port == 8443
    assert env.gateway_host == "127.0.0.1"
    assert env.gateway_max_concurrent == 4
    assert env.gateway_approval_timeout == 600
    assert env.gateway_gate_side_effects is True


def test_gateway_env_reads_prefixed_env(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "shh")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_PORT", "9000")
    env = GatewayEnv()
    assert env.bot_token == "tok"
    assert env.webhook_url == "https://example.test/hook"
    assert env.webhook_secret == "shh"
    assert env.webhook_port == 9000


def test_gateway_env_parses_allowed_users_csv(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", " 42, 99 ,, 7 ")
    env = GatewayEnv()
    assert env.allowed_users == ["42", "99", "7"]


def test_gateway_env_blank_host_falls_back_to_default(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_GATEWAY_HOST", "   ")
    env = GatewayEnv()
    assert env.gateway_host == "127.0.0.1"


@pytest.mark.parametrize("port", ["0", "70000"])
def test_gateway_env_rejects_out_of_range_port(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_PORT", port)
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        GatewayEnv()


# ---------------------------------------------------------------------------
# GatewaySettings webhook validation
# ---------------------------------------------------------------------------


def test_settings_webhook_url_requires_secret() -> None:
    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET is required"):
        GatewaySettings(bot_token="tok", webhook_url="https://example.test/hook")


def test_settings_webhook_url_with_secret_is_valid() -> None:
    settings = GatewaySettings(
        bot_token="tok",
        webhook_url="https://example.test/hook",
        webhook_secret="shh",
    )
    assert settings.webhook_secret == "shh"


# ---------------------------------------------------------------------------
# load_telegram_credentials
# ---------------------------------------------------------------------------


def test_load_credentials_returns_credentials_mapping() -> None:
    record = {"credentials": {"bot_token": "from-store"}}
    with patch(_STORE_PATH, return_value=record):
        assert load_telegram_credentials() == {"bot_token": "from-store"}


def test_load_credentials_no_record_returns_empty() -> None:
    with patch(_STORE_PATH, return_value=None):
        assert load_telegram_credentials() == {}


def test_load_credentials_no_credentials_key_returns_empty() -> None:
    with patch(_STORE_PATH, return_value={"name": "telegram"}):
        assert load_telegram_credentials() == {}


def test_load_credentials_store_failure_raises() -> None:
    with (
        patch(_STORE_PATH, side_effect=RuntimeError("boom")),
        pytest.raises(GatewayConfigurationError, match="Could not load Telegram"),
    ):
        load_telegram_credentials()


# ---------------------------------------------------------------------------
# store_bot_token / store_allowed_users
# ---------------------------------------------------------------------------


def test_store_bot_token_strips_value() -> None:
    assert store_bot_token({"bot_token": "  tok  "}) == "tok"


def test_store_bot_token_missing_returns_empty() -> None:
    assert store_bot_token({}) == ""


def test_store_allowed_users_no_policy_returns_empty() -> None:
    assert store_allowed_users({}) == []


def test_store_allowed_users_reads_policy_ids() -> None:
    policy = MessagingIdentityPolicy(allowed_user_ids=["42", "99"]).model_dump()
    assert store_allowed_users({"identity_policy": policy}) == ["42", "99"]


def test_store_allowed_users_non_mapping_policy_raises() -> None:
    with pytest.raises(GatewayConfigurationError, match="must be an object"):
        store_allowed_users({"identity_policy": "nope"})


def test_store_allowed_users_invalid_policy_raises() -> None:
    with pytest.raises(GatewayConfigurationError, match="Invalid Telegram identity_policy"):
        store_allowed_users({"identity_policy": {"allowed_user_ids": "not-a-list"}})


# ---------------------------------------------------------------------------
# choose_bot_token / choose_authorized_users
# ---------------------------------------------------------------------------


def test_choose_bot_token_prefers_env(clean_env: None) -> None:
    env = GatewayEnv(bot_token="env-tok")
    assert choose_bot_token(env, {"bot_token": "store-tok"}) == "env-tok"


def test_choose_bot_token_falls_back_to_store(clean_env: None) -> None:
    env = GatewayEnv()
    assert choose_bot_token(env, {"bot_token": "store-tok"}) == "store-tok"


def test_choose_bot_token_missing_raises(clean_env: None) -> None:
    env = GatewayEnv()
    with pytest.raises(GatewayConfigurationError, match="bot token is missing"):
        choose_bot_token(env, {})


def test_choose_authorized_users_prefers_store(clean_env: None) -> None:
    env = GatewayEnv(allowed_users=["1"])
    policy = MessagingIdentityPolicy(allowed_user_ids=["42"]).model_dump()
    assert choose_authorized_users(env, {"identity_policy": policy}) == ["42"]


def test_choose_authorized_users_falls_back_to_env(clean_env: None) -> None:
    env = GatewayEnv(allowed_users=["1", "2"])
    assert choose_authorized_users(env, {}) == ["1", "2"]


def test_choose_authorized_users_empty_warns(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    env = GatewayEnv()
    with caplog.at_level("WARNING"):
        assert choose_authorized_users(env, {}) == []
    assert "allowed users are not configured" in caplog.text


# ---------------------------------------------------------------------------
# load_gateway_settings (composition root)
# ---------------------------------------------------------------------------


def test_load_gateway_settings_env_and_store(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "shh")
    monkeypatch.setenv("TELEGRAM_GATEWAY_MAX_CONCURRENT", "8")
    policy = MessagingIdentityPolicy(allowed_user_ids=["42"]).model_dump()
    record = {"credentials": {"bot_token": "store-tok", "identity_policy": policy}}

    with patch(_STORE_PATH, return_value=record):
        settings = load_gateway_settings()

    assert isinstance(settings, GatewaySettings)
    assert settings.bot_token == "store-tok"
    assert settings.webhook_url == "https://example.test/hook"
    assert settings.webhook_secret == "shh"
    assert settings.allowed_user_ids == ["42"]
    assert settings.max_concurrent_turns == 8


def test_load_gateway_settings_missing_token_raises(
    clean_env: None,
) -> None:
    with (
        patch(_STORE_PATH, return_value=None),
        pytest.raises(GatewayConfigurationError, match="bot token is missing"),
    ):
        load_gateway_settings()


def test_load_gateway_settings_webhook_without_secret_raises(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://example.test/hook")

    with (
        patch(_STORE_PATH, return_value=None),
        pytest.raises(GatewayConfigurationError, match="Invalid Telegram gateway"),
    ):
        load_gateway_settings()
