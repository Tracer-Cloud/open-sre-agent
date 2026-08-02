"""Tests for MattermostSendMessageTool - Mattermost message action surface."""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest

from integrations.mattermost.tools.mattermost_send_message_tool import (
    MattermostSendMessageTool,
    mattermost_send_message,
)

_TOOL_PACKAGE = "integrations.mattermost.tools.mattermost_send_message_tool"

_PAT_CONFIG = {
    "server_url": "https://chat.example.com",
    "auth_token": "tok",
    "default_channel": "chan-1",
    "webhook_url": "",
}

_WEBHOOK_CONFIG = {
    "server_url": "",
    "auth_token": "",
    "default_channel": None,
    "webhook_url": "https://chat.example.com/hooks/abc",
}


@pytest.fixture
def mattermost_source() -> dict[str, Any]:
    """The flat/runtime ``sources`` shape passed to is_available()."""
    return {"mattermost": dict(_PAT_CONFIG)}


def _patch_config(monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]) -> None:
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery._resolved_config",
        lambda: dict(config),
    )


# ---------------------------------------------------------------------------
# Metadata and registry surface
# ---------------------------------------------------------------------------


def test_metadata_declares_mattermost_source() -> None:
    metadata = MattermostSendMessageTool.metadata()
    assert metadata.name == "mattermost_send_message"
    assert metadata.source == "mattermost"
    assert metadata.side_effect_level == "external"
    assert mattermost_send_message.requires_approval is True


def test_registered_tool_is_scoped_off_the_chat_surface() -> None:
    # Not on the gateway chat surface: the reply sink delivers gateway messages,
    # so exposing a send tool there lets the agent target the wrong platform.
    registered = mattermost_send_message.__opensre_registered_tool__
    assert registered.surfaces == ("investigation", "action")
    assert registered.requires_approval is True


def test_init_is_only_registry_entrypoint() -> None:
    package = importlib.import_module(_TOOL_PACKAGE)
    source = inspect.getsource(package)
    assert f"from {_TOOL_PACKAGE}.tool import" in source
    assert "class MattermostSendMessageTool" not in source


# ---------------------------------------------------------------------------
# is_available / extract_params
# ---------------------------------------------------------------------------


def test_is_available_true_with_pat(mattermost_source: dict[str, Any]) -> None:
    assert mattermost_send_message.is_available(mattermost_source) is True


def test_is_available_true_with_webhook_only() -> None:
    assert mattermost_send_message.is_available({"mattermost": dict(_WEBHOOK_CONFIG)}) is True


def test_is_available_false_when_not_configured() -> None:
    assert mattermost_send_message.is_available({}) is False


def test_is_available_false_when_pat_incomplete(mattermost_source: dict[str, Any]) -> None:
    mattermost_source["mattermost"]["auth_token"] = ""
    assert mattermost_send_message.is_available(mattermost_source) is False


def test_extract_params_returns_no_credentials(mattermost_source: dict[str, Any]) -> None:
    """extract_params output is serialized into traces - it must hold no secrets."""
    params = mattermost_send_message.extract_params(mattermost_source)
    assert params == {}


# ---------------------------------------------------------------------------
# run() — token mode
# ---------------------------------------------------------------------------


def test_run_resolves_credentials_internally_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _patch_config(monkeypatch, _PAT_CONFIG)

    def _fake_post(
        server_url: str, channel: str, text: str, auth_token: str
    ) -> tuple[bool, str, str]:
        captured.update(
            server_url=server_url,
            channel=channel,
            text=text,
            auth_token=auth_token,
        )
        return True, "", "m-1"

    monkeypatch.setattr(f"{_TOOL_PACKAGE}.delivery.post_mattermost_message", _fake_post)

    result = mattermost_send_message.run(message=" page on-call ", channel=" chan-ops ")

    assert result["status"] == "sent"
    assert result["sent"] is True
    assert result["channel"] == "chan-ops"
    assert result["message_length"] == len("page on-call")
    assert captured["server_url"] == "https://chat.example.com"
    assert captured["channel"] == "chan-ops"
    assert captured["text"] == "page on-call"
    assert captured["auth_token"] == "tok"


def test_run_falls_back_to_default_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _patch_config(monkeypatch, _PAT_CONFIG)
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery.post_mattermost_message",
        lambda *a, **_kw: captured.update(channel=a[1]) or (True, "", "m-1"),  # type: ignore[func-returns-value]
    )

    result = mattermost_send_message.run(message="hi")

    assert result["status"] == "sent"
    assert result["channel"] == "chan-1"
    assert captured["channel"] == "chan-1"


def test_run_truncates_long_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _patch_config(monkeypatch, _PAT_CONFIG)
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery.post_mattermost_message",
        lambda *a, **_kw: captured.update(text=a[2]) or (True, "", "m-1"),  # type: ignore[func-returns-value]
    )

    result = mattermost_send_message.run(message="x" * 5000)

    assert len(captured["text"]) == 4096
    assert captured["text"].endswith("…")
    # message_length reports what was actually delivered, not the raw input.
    assert result["message_length"] == 4096


# ---------------------------------------------------------------------------
# run() — webhook mode
# ---------------------------------------------------------------------------


def test_run_webhook_only_dispatches_via_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _patch_config(monkeypatch, _WEBHOOK_CONFIG)

    def _fake_webhook(webhook_url: str, text: str) -> tuple[bool, str]:
        captured.update(webhook_url=webhook_url, text=text)
        return True, ""

    monkeypatch.setattr(f"{_TOOL_PACKAGE}.delivery.post_mattermost_webhook", _fake_webhook)

    result = mattermost_send_message.run(message="failover complete")

    assert result["status"] == "sent"
    assert result["channel"] == "<webhook destination>"
    assert captured["webhook_url"] == _WEBHOOK_CONFIG["webhook_url"]
    assert captured["text"] == "failover complete"


def test_run_webhook_only_rejects_explicit_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, _WEBHOOK_CONFIG)

    result = mattermost_send_message.run(message="hi", channel="chan-ops")

    assert result["status"] == "failed"
    assert result["error_type"] == "configuration_error"
    assert "fixed" in result["error"].lower()


def test_run_webhook_delivery_failure_reports_display_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(monkeypatch, _WEBHOOK_CONFIG)
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery.post_mattermost_webhook",
        lambda *_a, **_kw: (False, "webhook disabled"),
    )

    result = mattermost_send_message.run(message="hi")

    assert result["status"] == "failed"
    assert result["error_type"] == "delivery_error"
    # Failure path mirrors the success path — never an empty channel and
    # never the webhook URL (it embeds a token).
    assert result["channel"] == "<webhook destination>"


def test_run_pat_without_channel_errors_even_with_webhook_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mixed PAT+webhook setup with no channel must not silently fall back
    to the webhook's fixed destination."""
    _patch_config(
        monkeypatch,
        {
            **_PAT_CONFIG,
            "default_channel": None,
            "webhook_url": _WEBHOOK_CONFIG["webhook_url"],
        },
    )

    result = mattermost_send_message.run(message="hi")

    assert result["status"] == "failed"
    assert result["error_type"] == "configuration_error"
    assert "channel" in result["error"].lower()


def test_run_prefers_token_mode_when_both_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _patch_config(monkeypatch, {**_PAT_CONFIG, "webhook_url": _WEBHOOK_CONFIG["webhook_url"]})
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery.post_mattermost_message",
        lambda *a, **_kw: captured.update(channel=a[1]) or (True, "", "m-1"),  # type: ignore[func-returns-value]
    )

    result = mattermost_send_message.run(message="hi", channel="chan-ops")

    assert result["status"] == "sent"
    assert captured["channel"] == "chan-ops"


# ---------------------------------------------------------------------------
# run() — failures
# ---------------------------------------------------------------------------


def test_run_failed_when_message_is_empty() -> None:
    result = mattermost_send_message.run(message="  ", channel="chan-ops")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["available"] is True
    assert result["error_type"] == "validation_error"
    assert "empty" in result["error"].lower()


def test_run_failed_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, {})

    result = mattermost_send_message.run(message="hi", channel="chan-ops")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["available"] is False
    assert result["error_type"] == "configuration_error"
    assert "not configured" in result["error"].lower()


def test_run_failed_when_pat_has_no_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, {**_PAT_CONFIG, "default_channel": None})

    result = mattermost_send_message.run(message="hi")

    assert result["status"] == "failed"
    assert result["error_type"] == "configuration_error"
    assert "channel" in result["error"].lower()


def test_run_propagates_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, _PAT_CONFIG)
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery.post_mattermost_message",
        lambda *_a, **_kw: (False, "channel-not-found", ""),
    )

    result = mattermost_send_message.run(message="hi", channel="chan-nope")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["error"] == "channel-not-found"
    assert result["error_type"] == "delivery_error"
    assert result["channel"] == "chan-nope"
