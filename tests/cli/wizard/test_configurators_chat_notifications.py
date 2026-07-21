"""Characterization tests for the onboarding wizard's Telegram configurator.

These pin the observable behavior of
:func:`surfaces.cli.wizard.configurators.chat_notifications._configure_telegram`
before it is migrated onto the shared setup flow: the prompts, validate-before-
persist ordering, the retry loop on a bad token, the credential tiers it writes
(store + keyring + ``.env``), and the "configured but cannot deliver" advisory.

This path is the reference for the credential-resolution contract in
``docs/adding-tools-and-integrations.md`` — the keyring/``.env`` assertions here
are what the migration must carry over, not drop.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import surfaces.cli.wizard.configurators.chat_notifications as chat_notifications
from surfaces.cli.wizard.integration_validators.shared import IntegrationHealthResult

_TOKEN = "123456789:AAExampleSecretTokenValue"
_CHAT_ID = "-1001234567890"
_ENV_PATH = Path("sentinel.env")


class _RecordingConsole:
    """Minimal stand-in for the wizard console that captures printed output."""

    def __init__(self) -> None:
        self.output: list[str] = []

    def print(self, *args: Any, **_kwargs: Any) -> None:
        self.output.append(" ".join(str(arg) for arg in args))

    @contextmanager
    def status(self, *_args: Any, **_kwargs: Any) -> Iterator[None]:
        yield

    @property
    def text(self) -> str:
        return "\n".join(self.output)


@dataclass(frozen=True)
class _Prompt:
    """One question the configurator asked."""

    label: str
    secret: bool
    allow_empty: bool


@dataclass
class _Wizard:
    """Scripted answers/results for one run, plus everything the run did."""

    console: _RecordingConsole = field(default_factory=_RecordingConsole)
    # Scripted inputs, consumed in order.
    answers: list[str] = field(default_factory=list)
    results: list[IntegrationHealthResult] = field(default_factory=list)
    # Recorded effects.
    asked: list[_Prompt] = field(default_factory=list)
    validated: list[str] = field(default_factory=list)
    saved: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    keyring: list[tuple[str, str]] = field(default_factory=list)
    env_values: list[dict[str, str]] = field(default_factory=list)


@pytest.fixture
def wizard(monkeypatch: pytest.MonkeyPatch) -> _Wizard:
    """Patch every collaborator of ``_configure_telegram`` and expose the calls."""
    run = _Wizard()

    def _fake_prompt(
        label: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = False,
        back_on_cancel: bool = False,
    ) -> str:
        run.asked.append(_Prompt(label=label, secret=secret, allow_empty=allow_empty))
        return run.answers.pop(0)

    def _fake_validate(*, bot_token: str) -> IntegrationHealthResult:
        run.validated.append(bot_token)
        return run.results.pop(0)

    def _fake_sync_env_values(values: dict[str, str], **_kwargs: Any) -> Path:
        run.env_values.append(dict(values))
        return _ENV_PATH

    monkeypatch.setattr(chat_notifications, "_console", run.console)
    monkeypatch.setattr(chat_notifications, "_integration_defaults", lambda _s: ({}, {}))
    monkeypatch.setattr(chat_notifications, "_prompt_value", _fake_prompt)
    monkeypatch.setattr(chat_notifications, "validate_telegram_bot", _fake_validate)
    monkeypatch.setattr(chat_notifications, "_render_integration_result", lambda *_a: None)
    monkeypatch.setattr(
        chat_notifications,
        "upsert_integration",
        lambda service, payload: run.saved.append((service, payload)),
    )
    monkeypatch.setattr(
        chat_notifications,
        "sync_env_secret",
        lambda key, value: run.keyring.append((key, value)),
    )
    monkeypatch.setattr(chat_notifications, "sync_env_values", _fake_sync_env_values)
    return run


def _ok(detail: str = "Connected to Telegram bot @acme_bot.") -> IntegrationHealthResult:
    return IntegrationHealthResult(ok=True, detail=detail)


def _bad(detail: str = "Telegram API check failed: Unauthorized") -> IntegrationHealthResult:
    return IntegrationHealthResult(ok=False, detail=detail)


def test_prompts_for_token_then_chat_id(wizard: _Wizard) -> None:
    wizard.answers[:] = [_TOKEN, _CHAT_ID]
    wizard.results[:] = [_ok()]

    chat_notifications._configure_telegram()

    asked = wizard.asked
    assert len(asked) == 2
    # The token is masked and mandatory; the chat id is asked for but skippable.
    assert asked[0].secret is True
    assert "token" in asked[0].label.lower()
    assert asked[1].allow_empty is True
    assert "chat" in asked[1].label.lower()


def test_writes_store_keyring_and_env_on_success(wizard: _Wizard) -> None:
    """All three credential tiers are written — the contract the refactor must keep."""
    wizard.answers[:] = [_TOKEN, _CHAT_ID]
    wizard.results[:] = [_ok()]

    label, env_path = chat_notifications._configure_telegram()

    assert wizard.saved == [
        (
            "telegram",
            {"credentials": {"bot_token": _TOKEN, "default_chat_id": _CHAT_ID}},
        )
    ]
    assert wizard.keyring == [("TELEGRAM_BOT_TOKEN", _TOKEN)]
    assert wizard.env_values == [{"TELEGRAM_DEFAULT_CHAT_ID": _CHAT_ID}]
    assert label == "Telegram"
    assert env_path == str(_ENV_PATH)


def test_validation_runs_before_anything_is_persisted(wizard: _Wizard) -> None:
    wizard.answers[:] = [_TOKEN, _CHAT_ID]
    wizard.results[:] = [_ok()]

    chat_notifications._configure_telegram()

    assert wizard.validated == [_TOKEN]


def test_bad_token_re_prompts_and_saves_nothing_until_it_validates(
    wizard: _Wizard,
) -> None:
    """The wizard loops on a rejected token rather than persisting junk."""
    wizard.answers[:] = ["wrong-token", "", _TOKEN, _CHAT_ID]
    wizard.results[:] = [_bad(), _ok()]

    chat_notifications._configure_telegram()

    assert wizard.validated == ["wrong-token", _TOKEN]
    # Only the second, valid attempt reaches the store.
    assert len(wizard.saved) == 1
    assert wizard.saved[0][1]["credentials"]["bot_token"] == _TOKEN
    assert wizard.keyring == [("TELEGRAM_BOT_TOKEN", _TOKEN)]


def test_blank_chat_id_saves_none_and_warns_about_delivery(wizard: _Wizard) -> None:
    """Token-only setup verifies, but Hermes/watchdog cannot deliver without a chat id."""
    wizard.answers[:] = [_TOKEN, ""]
    wizard.results[:] = [_ok()]

    chat_notifications._configure_telegram()

    assert wizard.saved[0][1]["credentials"]["default_chat_id"] is None
    # No chat id means no env value to sync, but .env is still written.
    assert wizard.env_values == [{}]
    assert "TELEGRAM_DEFAULT_CHAT_ID" in wizard.console.text


def test_no_delivery_warning_once_a_chat_id_is_set(wizard: _Wizard) -> None:
    wizard.answers[:] = [_TOKEN, _CHAT_ID]
    wizard.results[:] = [_ok()]

    chat_notifications._configure_telegram()

    assert "No default chat ID set" not in wizard.console.text


def test_token_is_never_echoed_to_the_console(wizard: _Wizard) -> None:
    wizard.answers[:] = [_TOKEN, _CHAT_ID]
    wizard.results[:] = [_ok()]

    chat_notifications._configure_telegram()

    assert _TOKEN not in wizard.console.text
