"""Tests for integrations.mattermost.credentials."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.mattermost.credentials import (
    MattermostCredentials,
    load_credentials_from_env,
)
from platform.common.errors import OpenSREError

_MATTERMOST_ENV_VARS = (
    "MATTERMOST_SERVER_URL",
    "MATTERMOST_AUTH_TOKEN",
    "MATTERMOST_WEBHOOK_URL",
    "MATTERMOST_DEFAULT_CHANNEL",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make credential resolution hermetic — no local machine leakage.

    Patches the real underlying store lookup (``resolve_effective_integrations``)
    rather than ``_store_default_channel`` itself, so tests exercise the real
    channel-resolution code path (including its store-failure resilience)
    instead of a stub that would hide it.
    """
    for env_var in _MATTERMOST_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(
        "platform.scheduler.credentials._get_integration_credential",
        lambda *_: "",
    )
    monkeypatch.setattr(
        "integrations.catalog.resolve_effective_integrations",
        lambda: {},
    )


def test_credentials_repr_does_not_leak_auth_token_or_webhook_url() -> None:
    creds = MattermostCredentials(
        server_url="https://chat.example.com",
        auth_token="super-secret-token",
        webhook_url="https://chat.example.com/hooks/super-secret-hook-token",
        channel="chan-1",
    )
    rendered = repr(creds)
    assert "super-secret-token" not in rendered
    assert "super-secret-hook-token" not in rendered
    assert "chan-1" in rendered


def test_token_mode_with_channel_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATTERMOST_SERVER_URL", "https://chat.example.com")
    monkeypatch.setenv("MATTERMOST_AUTH_TOKEN", "tok")

    creds = load_credentials_from_env(channel_override="chan-ops")

    assert creds.has_token is True
    assert creds.channel == "chan-ops"


def test_token_mode_falls_back_to_default_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATTERMOST_SERVER_URL", "https://chat.example.com")
    monkeypatch.setenv("MATTERMOST_AUTH_TOKEN", "tok")
    monkeypatch.setenv("MATTERMOST_DEFAULT_CHANNEL", "chan-incidents")

    creds = load_credentials_from_env()

    assert creds.channel == "chan-incidents"


def test_token_mode_without_any_channel_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATTERMOST_SERVER_URL", "https://chat.example.com")
    monkeypatch.setenv("MATTERMOST_AUTH_TOKEN", "tok")

    with pytest.raises(OpenSREError) as exc_info:
        load_credentials_from_env()
    assert "channel" in str(exc_info.value).lower()


def test_webhook_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATTERMOST_WEBHOOK_URL", "https://chat.example.com/hooks/abc")

    creds = load_credentials_from_env()

    assert creds.has_token is False
    assert creds.webhook_url == "https://chat.example.com/hooks/abc"


def test_webhook_only_rejects_explicit_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATTERMOST_WEBHOOK_URL", "https://chat.example.com/hooks/abc")

    with pytest.raises(OpenSREError) as exc_info:
        load_credentials_from_env(channel_override="chan-ops")
    assert "token credentials" in str(exc_info.value).lower()


def test_token_mode_preferred_over_webhook_when_both_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MATTERMOST_SERVER_URL", "https://chat.example.com")
    monkeypatch.setenv("MATTERMOST_AUTH_TOKEN", "tok")
    monkeypatch.setenv("MATTERMOST_WEBHOOK_URL", "https://chat.example.com/hooks/abc")
    monkeypatch.setenv("MATTERMOST_DEFAULT_CHANNEL", "chan-incidents")

    creds = load_credentials_from_env()

    assert creds.has_token is True
    assert creds.channel == "chan-incidents"


def test_token_without_channel_never_falls_back_to_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed config: full token + webhook + no channel must error, not
    silently fall back to the webhook's fixed destination."""
    monkeypatch.setenv("MATTERMOST_SERVER_URL", "https://chat.example.com")
    monkeypatch.setenv("MATTERMOST_AUTH_TOKEN", "tok")
    monkeypatch.setenv("MATTERMOST_WEBHOOK_URL", "https://chat.example.com/hooks/abc")

    with pytest.raises(OpenSREError) as exc_info:
        load_credentials_from_env()
    assert "channel" in str(exc_info.value).lower()


def test_nothing_configured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(OpenSREError) as exc_info:
        load_credentials_from_env()
    assert "not configured" in str(exc_info.value).lower()


def test_channel_override_beats_default_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATTERMOST_SERVER_URL", "https://chat.example.com")
    monkeypatch.setenv("MATTERMOST_AUTH_TOKEN", "tok")
    monkeypatch.setenv("MATTERMOST_DEFAULT_CHANNEL", "chan-default")

    creds = load_credentials_from_env(channel_override="chan-explicit")

    assert creds.channel == "chan-explicit"


def test_blank_channel_override_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATTERMOST_SERVER_URL", "https://chat.example.com")
    monkeypatch.setenv("MATTERMOST_AUTH_TOKEN", "tok")
    monkeypatch.setenv("MATTERMOST_DEFAULT_CHANNEL", "chan-default")

    creds = load_credentials_from_env(channel_override="   ")

    assert creds.channel == "chan-default"


def test_real_store_default_channel_resilient_to_store_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> Any:
        raise RuntimeError("store is locked")

    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", _boom)

    from integrations.mattermost.credentials import _store_default_channel

    assert _store_default_channel() == ""


def test_real_store_default_channel_resilient_to_none_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_effective_integrations() returning None (not a dict) must not
    raise AttributeError from the unguarded .get('mattermost', {}) call."""
    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", lambda: None)

    from integrations.mattermost.credentials import _store_default_channel

    assert _store_default_channel() == ""
