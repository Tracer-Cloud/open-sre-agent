"""Tests for BuzzSendMessageTool - Buzz message action surface."""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest

from integrations.buzz.tools.buzz_send_message_tool import (
    BuzzSendMessageTool,
    buzz_send_message,
)

_TOOL_PACKAGE = "integrations.buzz.tools.buzz_send_message_tool"

_CONFIG = {
    "relay_url": "http://localhost:3000",
    "private_key": "nsec1x",
    "auth_tag": "",
    "buzz_path": "buzz",
    "default_channel": "chan-default",
}


@pytest.fixture
def buzz_source() -> dict[str, Any]:
    """The flat/runtime ``sources`` shape passed to is_available()."""
    return {"buzz": dict(_CONFIG)}


def _patch_config(monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]) -> None:
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery._resolved_config",
        lambda: dict(config),
    )


# ---------------------------------------------------------------------------
# Metadata and registry surface
# ---------------------------------------------------------------------------


def test_metadata_and_registration() -> None:
    metadata = BuzzSendMessageTool.metadata()
    assert metadata.name == "buzz_send_message"
    assert metadata.source == "buzz"
    assert metadata.side_effect_level == "external"
    assert buzz_send_message.requires_approval is True
    # Not on the gateway chat surface: the reply sink delivers gateway messages,
    # so exposing a send tool there lets the agent target the wrong platform.
    registered = buzz_send_message.__opensre_registered_tool__
    assert registered.surfaces == ("investigation", "action")


def test_init_is_only_registry_entrypoint() -> None:
    package = importlib.import_module(_TOOL_PACKAGE)
    source = inspect.getsource(package)
    assert f"from {_TOOL_PACKAGE}.tool import" in source
    assert "class BuzzSendMessageTool" not in source


# ---------------------------------------------------------------------------
# is_available / extract_params
# ---------------------------------------------------------------------------


def test_is_available_true_with_private_key(buzz_source: dict[str, Any]) -> None:
    assert buzz_send_message.is_available(buzz_source) is True


def test_is_available_false_when_not_configured() -> None:
    assert buzz_send_message.is_available({}) is False


def test_extract_params_returns_no_credentials(buzz_source: dict[str, Any]) -> None:
    """extract_params output is serialized into traces - it must hold no secrets."""
    params = buzz_send_message.extract_params(buzz_source)
    assert params == {}


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


def test_run_resolves_credentials_internally_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _patch_config(monkeypatch, _CONFIG)

    def _fake_post(
        relay_url: str, channel: str, text: str, private_key: str, **kwargs: Any
    ) -> tuple[bool, str, str]:
        captured.update(
            relay_url=relay_url,
            channel=channel,
            text=text,
            private_key=private_key,
            **kwargs,
        )
        return True, "", "evt-1"

    monkeypatch.setattr(f"{_TOOL_PACKAGE}.delivery.post_buzz_message", _fake_post)

    result = buzz_send_message.run(message=" page on-call ", channel=" chan-explicit ")

    assert result["status"] == "sent"
    assert result["sent"] is True
    assert result["channel"] == "chan-explicit"
    assert result["message_length"] == len("page on-call")
    assert captured["relay_url"] == "http://localhost:3000"
    assert captured["channel"] == "chan-explicit"
    assert captured["text"] == "page on-call"
    assert captured["private_key"] == "nsec1x"


def test_run_falls_back_to_default_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _patch_config(monkeypatch, _CONFIG)
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery.post_buzz_message",
        lambda *a, **_kw: captured.update(channel=a[1]) or (True, "", "evt-1"),  # type: ignore[func-returns-value]
    )

    result = buzz_send_message.run(message="hi")

    assert result["status"] == "sent"
    assert result["channel"] == "chan-default"
    assert captured["channel"] == "chan-default"


def test_run_truncates_long_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _patch_config(monkeypatch, _CONFIG)
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery.post_buzz_message",
        lambda *a, **_kw: captured.update(text=a[2]) or (True, "", "evt-1"),  # type: ignore[func-returns-value]
    )

    result = buzz_send_message.run(message="x" * 5000)

    assert len(captured["text"]) == 4096
    assert captured["text"].endswith("…")
    assert result["message_length"] == 4096


# ---------------------------------------------------------------------------
# run() — failures
# ---------------------------------------------------------------------------


def test_run_failed_when_message_is_empty() -> None:
    result = buzz_send_message.run(message="  ", channel="chan-uuid")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["available"] is True
    assert result["error_type"] == "validation_error"
    assert "empty" in result["error"].lower()


def test_run_failed_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, {})

    result = buzz_send_message.run(message="hi", channel="chan-uuid")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["available"] is False
    assert result["error_type"] == "configuration_error"
    assert "not configured" in result["error"].lower()


def test_run_failed_when_no_channel_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, {**_CONFIG, "default_channel": ""})

    result = buzz_send_message.run(message="hi")

    assert result["status"] == "failed"
    assert result["error_type"] == "configuration_error"
    assert "channel" in result["error"].lower()


def test_run_propagates_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, _CONFIG)
    monkeypatch.setattr(
        f"{_TOOL_PACKAGE}.delivery.post_buzz_message",
        lambda *_a, **_kw: (False, "channel not found", ""),
    )

    result = buzz_send_message.run(message="hi", channel="chan-nope")

    assert result["status"] == "failed"
    assert result["sent"] is False
    assert result["error"] == "channel not found"
    assert result["error_type"] == "delivery_error"
    assert result["channel"] == "chan-nope"
