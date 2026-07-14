from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from gateway.runtime.errors import GatewayConfigurationError
from gateway.slack.settings import load_slack_gateway_settings

_STORE_PATH = "gateway.slack.settings.get_integration"

_TOKEN_VARS = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_APP_TOKEN": "xapp-test",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove all SLACK_* env vars so SlackGatewayEnv falls back to defaults.

    The root conftest loads a local ``.env`` into ``os.environ``; without this
    the gateway env settings would be non-deterministic across machines.
    """
    for key in list(os.environ):
        if key.startswith("SLACK_"):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def empty_store() -> Iterator[None]:
    """Default the integration store to unconfigured; store tests override."""
    with patch(_STORE_PATH, return_value=None):
        yield


def _set_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _TOKEN_VARS.items():
        monkeypatch.setenv(key, value)


def _store_record(credentials: dict[str, Any]) -> dict[str, Any]:
    return {"credentials": credentials}


def test_loads_tokens_with_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U111")

    settings = load_slack_gateway_settings()

    assert settings.bot_token == "xoxb-test"
    assert settings.app_token == "xapp-test"
    assert settings.allowed_user_ids == ["U111"]
    assert settings.allow_open_workspace is False
    assert settings.max_concurrent_turns == 4


def test_parses_allowed_users_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U111, U222 ,,U333")

    settings = load_slack_gateway_settings()

    assert settings.allowed_user_ids == ["U111", "U222", "U333"]


def test_empty_allowlist_requires_open_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)

    with pytest.raises(GatewayConfigurationError, match="allowed users"):
        load_slack_gateway_settings()


def test_open_workspace_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)
    monkeypatch.setenv("SLACK_ALLOW_OPEN_WORKSPACE", "1")

    settings = load_slack_gateway_settings()

    assert settings.allowed_user_ids == []
    assert settings.allow_open_workspace is True


@pytest.mark.parametrize("missing", ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"])
def test_missing_token_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _set_tokens(monkeypatch)
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U111")
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(GatewayConfigurationError):
        load_slack_gateway_settings()


# ---------------------------------------------------------------------------
# Integration-store credentials (same precedence rules as the Telegram loader)
# ---------------------------------------------------------------------------


def test_store_supplies_tokens_and_allowlist() -> None:
    record = _store_record(
        {
            "bot_token": "xoxb-store",
            "app_token": "xapp-store",
            "identity_policy": {"inbound_enabled": True, "allowed_user_ids": ["U777"]},
        }
    )

    with patch(_STORE_PATH, return_value=record):
        settings = load_slack_gateway_settings()

    assert settings.bot_token == "xoxb-store"
    assert settings.app_token == "xapp-store"
    assert settings.allowed_user_ids == ["U777"]


def test_env_tokens_win_over_store(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)
    record = _store_record(
        {
            "bot_token": "xoxb-store",
            "app_token": "xapp-store",
            "identity_policy": {"inbound_enabled": True, "allowed_user_ids": ["U777"]},
        }
    )

    with patch(_STORE_PATH, return_value=record):
        settings = load_slack_gateway_settings()

    assert settings.bot_token == "xoxb-test"
    assert settings.app_token == "xapp-test"


def test_store_allowlist_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U111")
    record = _store_record(
        {"identity_policy": {"inbound_enabled": True, "allowed_user_ids": ["U777"]}}
    )

    with patch(_STORE_PATH, return_value=record):
        settings = load_slack_gateway_settings()

    assert settings.allowed_user_ids == ["U777"]


def test_store_allowlist_satisfies_deny_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)
    record = _store_record(
        {"identity_policy": {"inbound_enabled": True, "allowed_user_ids": ["U777"]}}
    )

    with patch(_STORE_PATH, return_value=record):
        settings = load_slack_gateway_settings()

    assert settings.allowed_user_ids == ["U777"]
    assert settings.allow_open_workspace is False


def test_invalid_store_identity_policy_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)
    record = _store_record({"identity_policy": "not-an-object"})

    with (
        patch(_STORE_PATH, return_value=record),
        pytest.raises(GatewayConfigurationError, match="identity_policy"),
    ):
        load_slack_gateway_settings()
