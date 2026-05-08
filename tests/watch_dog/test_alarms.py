"""Tests for app/watch_dog/alarms.py."""

from __future__ import annotations

from typing import Any

import pytest

from app.cli.support.errors import OpenSREError
from app.watch_dog.alarms import (
    AlarmCredentials,
    AlarmDispatcher,
    load_credentials_from_env,
)


def _stub_telegram(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ok: bool = True,
    error: str = "",
    captured: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = captured if captured is not None else []

    def _fake_post(
        chat_id: str,
        text: str,
        bot_token: str,
    ) -> tuple[bool, str, str]:
        calls.append({"chat_id": chat_id, "text": text, "bot_token": bot_token})
        return ok, error, "1" if ok else ""

    monkeypatch.setattr(
        "app.watch_dog.alarms.post_telegram_message",
        _fake_post,
    )
    return calls


def _patch_clock(monkeypatch: pytest.MonkeyPatch, ticks: list[float]) -> None:
    iterator = iter(ticks)

    def _now() -> float:
        return next(iterator)

    monkeypatch.setattr(AlarmDispatcher, "_now", staticmethod(_now))


def test_alarm_credentials_repr_does_not_leak_bot_token() -> None:
    # Auto-generated dataclass __repr__ surfaces in pytest assertion output,
    # tracebacks, and structured log capture. The token must stay out of it.
    creds = AlarmCredentials(bot_token="super-secret-token", chat_id="chat-1")

    rendered = repr(creds)

    assert "super-secret-token" not in rendered
    assert "chat-1" in rendered


def test_load_credentials_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-123")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "chat-1")

    creds = load_credentials_from_env()

    assert creds == AlarmCredentials(bot_token="tok-123", chat_id="chat-1")


def test_load_credentials_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  tok-123  ")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "\tchat-1\n")

    creds = load_credentials_from_env()

    assert creds.bot_token == "tok-123"
    assert creds.chat_id == "chat-1"


def test_load_credentials_chat_id_override_beats_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "from-env")

    creds = load_credentials_from_env(chat_id_override="from-arg")

    assert creds.chat_id == "from-arg"


def test_load_credentials_missing_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "chat-1")

    with pytest.raises(OpenSREError) as exc_info:
        load_credentials_from_env()

    assert "TELEGRAM_BOT_TOKEN" in str(exc_info.value)
    assert exc_info.value.suggestion is not None
    assert "TELEGRAM_BOT_TOKEN" in exc_info.value.suggestion


def test_load_credentials_blank_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "chat-1")

    with pytest.raises(OpenSREError):
        load_credentials_from_env()


def test_load_credentials_missing_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.delenv("TELEGRAM_DEFAULT_CHAT_ID", raising=False)

    with pytest.raises(OpenSREError) as exc_info:
        load_credentials_from_env()

    assert "chat id" in str(exc_info.value).lower()


def test_load_credentials_missing_chat_id_with_blank_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "chat-from-env")

    # Empty string override falls through to env.
    creds = load_credentials_from_env(chat_id_override="")

    assert creds.chat_id == "chat-from-env"


def test_load_credentials_whitespace_override_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A whitespace-only override must fall through to the env var the same
    # way an empty-string override does, not raise a misleading error.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "chat-from-env")

    creds = load_credentials_from_env(chat_id_override="   ")

    assert creds.chat_id == "chat-from-env"


def test_first_dispatch_calls_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_telegram(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = AlarmDispatcher(
        AlarmCredentials(bot_token="tok", chat_id="chat-1"),
    )

    assert dispatcher.dispatch("max_cpu", "CPU pegged at 95%") is True
    assert len(calls) == 1
    assert calls[0] == {
        "chat_id": "chat-1",
        "text": "CPU pegged at 95%",
        "bot_token": "tok",
    }


def test_second_dispatch_within_cooldown_is_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_telegram(monkeypatch)
    # 100s gap < 300s cooldown, second call must be suppressed.
    _patch_clock(monkeypatch, [100.0, 200.0])

    dispatcher = AlarmDispatcher(
        AlarmCredentials(bot_token="tok", chat_id="chat-1"),
        cooldown_seconds=300.0,
    )

    assert dispatcher.dispatch("max_cpu", "first") is True
    assert dispatcher.dispatch("max_cpu", "second") is False
    assert len(calls) == 1
    assert calls[0]["text"] == "first"


def test_second_dispatch_after_cooldown_calls_telegram_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_telegram(monkeypatch)
    # 350s gap > 300s cooldown, second call must go through.
    _patch_clock(monkeypatch, [100.0, 450.0])

    dispatcher = AlarmDispatcher(
        AlarmCredentials(bot_token="tok", chat_id="chat-1"),
        cooldown_seconds=300.0,
    )

    assert dispatcher.dispatch("max_cpu", "first") is True
    assert dispatcher.dispatch("max_cpu", "second") is True
    assert len(calls) == 2
    assert calls[1]["text"] == "second"


def test_cooldown_is_per_threshold_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_telegram(monkeypatch)
    _patch_clock(monkeypatch, [100.0, 110.0])

    dispatcher = AlarmDispatcher(
        AlarmCredentials(bot_token="tok", chat_id="chat-1"),
        cooldown_seconds=300.0,
    )

    assert dispatcher.dispatch("max_cpu", "cpu") is True
    assert dispatcher.dispatch("max_runtime", "runtime") is True
    assert len(calls) == 2


def test_dispatch_returns_false_on_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A failed Telegram call must NOT arm the cooldown, otherwise a transient
    # outage would silently swallow the next real alarm.
    calls: list[dict[str, Any]] = []
    _stub_telegram(monkeypatch, ok=False, error="network down", captured=calls)
    _patch_clock(monkeypatch, [100.0, 105.0])

    dispatcher = AlarmDispatcher(
        AlarmCredentials(bot_token="tok", chat_id="chat-1"),
        cooldown_seconds=300.0,
    )

    assert dispatcher.dispatch("max_cpu", "first") is False
    assert dispatcher.dispatch("max_cpu", "second") is False
    assert len(calls) == 2


def test_dispatch_uses_credentials_from_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_telegram(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = AlarmDispatcher(
        AlarmCredentials(bot_token="bot-XYZ", chat_id="my-chat"),
    )
    dispatcher.dispatch("max_runtime", "process exceeded 5m")

    assert calls[0]["bot_token"] == "bot-XYZ"
    assert calls[0]["chat_id"] == "my-chat"


def test_dispatch_truncates_messages_over_telegram_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Telegram rejects messages over 4096 chars. Without truncation an
    # oversized alarm would always fail, never arm cooldown, and retry forever.
    calls = _stub_telegram(monkeypatch)
    _patch_clock(monkeypatch, [100.0])

    dispatcher = AlarmDispatcher(
        AlarmCredentials(bot_token="tok", chat_id="chat-1"),
    )
    oversized = "X" * 5000
    assert dispatcher.dispatch("max_cpu", oversized) is True
    assert len(calls[0]["text"]) <= 4096
    assert calls[0]["text"].endswith("…")
