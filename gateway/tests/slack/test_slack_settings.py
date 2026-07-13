from __future__ import annotations

import pytest

from gateway.runtime.errors import GatewayConfigurationError
from gateway.slack.settings import load_slack_gateway_settings

_TOKEN_VARS = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_APP_TOKEN": "xapp-test",
}


def _set_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _TOKEN_VARS.items():
        monkeypatch.setenv(key, value)


def test_loads_tokens_with_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)
    monkeypatch.setenv("SLACK_ALLOWED_USERS", "U111")
    monkeypatch.delenv("SLACK_ALLOW_OPEN_WORKSPACE", raising=False)

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
    monkeypatch.delenv("SLACK_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("SLACK_ALLOW_OPEN_WORKSPACE", raising=False)

    with pytest.raises(GatewayConfigurationError, match="SLACK_ALLOWED_USERS"):
        load_slack_gateway_settings()


def test_open_workspace_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tokens(monkeypatch)
    monkeypatch.delenv("SLACK_ALLOWED_USERS", raising=False)
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
