"""Behavior of the Telegram ``getChat`` resolution used during setup."""

from __future__ import annotations

import types

import pytest

import integrations.telegram.chat_lookup as chat_lookup

_TOKEN = "123456:SECRET-TOKEN-ABC"


def _respond(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> dict[str, object]:
    """Patch the API call and capture the params it was invoked with."""
    seen: dict[str, object] = {}

    def _get(_url: str, **kwargs: object) -> types.SimpleNamespace:
        seen.update(kwargs.get("params") or {})
        return types.SimpleNamespace(json=lambda: payload)

    monkeypatch.setattr(chat_lookup.requests, "get", _get)
    return seen


def test_channel_username_resolves_to_the_numeric_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: users can supply the @name, delivery gets the id."""
    seen = _respond(
        monkeypatch,
        {
            "ok": True,
            "result": {"id": -1001234567890, "title": "Acme Alerts", "type": "channel"},
        },
    )

    numeric_id, description, error = chat_lookup.resolve_chat_id(
        bot_token=_TOKEN, chat_id="@acme_alerts"
    )

    assert error == ""
    assert numeric_id == "-1001234567890"
    assert description == "Acme Alerts (channel)"
    assert seen["chat_id"] == "@acme_alerts"


def test_numeric_id_is_accepted_and_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    _respond(
        monkeypatch,
        {"ok": True, "result": {"id": -100999, "title": "SRE On-call", "type": "supergroup"}},
    )

    numeric_id, description, error = chat_lookup.resolve_chat_id(
        bot_token=_TOKEN, chat_id=" -100999 "
    )

    assert error == ""
    assert numeric_id == "-100999"
    assert description == "SRE On-call (supergroup)"


def test_direct_message_falls_back_to_the_account_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private chats carry no title, so the description uses first/last name."""
    _respond(
        monkeypatch,
        {
            "ok": True,
            "result": {"id": 555, "first_name": "Priya", "last_name": "B", "type": "private"},
        },
    )

    _, description, error = chat_lookup.resolve_chat_id(bot_token=_TOKEN, chat_id="555")

    assert error == ""
    assert description == "Priya B (private)"


def test_unreachable_chat_is_an_error_not_a_silent_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure this check exists for: bot was never added to the channel."""
    _respond(monkeypatch, {"ok": False, "description": "chat not found"})

    numeric_id, _, error = chat_lookup.resolve_chat_id(bot_token=_TOKEN, chat_id="@typo_channel")

    assert numeric_id == ""
    assert "@typo_channel" in error
    assert "chat not found" in error


def test_blank_reference_is_rejected_without_calling_the_api() -> None:
    numeric_id, _, error = chat_lookup.resolve_chat_id(bot_token=_TOKEN, chat_id="   ")

    assert numeric_id == ""
    assert "Missing chat id" in error


def test_transport_error_never_echoes_the_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise ConnectionError(f"url: https://api.telegram.org/bot{_TOKEN}/getChat")

    monkeypatch.setattr(chat_lookup.requests, "get", _boom)

    _, _, error = chat_lookup.resolve_chat_id(bot_token=_TOKEN, chat_id="@acme_alerts")

    assert _TOKEN not in error
    assert "<redacted>" in error
