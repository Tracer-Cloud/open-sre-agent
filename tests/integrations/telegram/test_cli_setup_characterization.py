"""Characterization tests for ``opensre integrations setup telegram``.

These pin the observable behavior of :func:`integrations.cli._setup_telegram`
before it is migrated onto the shared setup flow: what it prompts for, that it
verifies *before* it persists, and the exact credential shape it writes. They
are written against the pre-refactor implementation and must stay green through
the migration — anything they catch is an unintended behavior change.

What is deliberately *not* pinned here: the credential tiers written. This path
saves to the integration store only, while the onboarding wizard also writes the
keyring and ``.env``. Unifying that is the point of the refactor, so asserting
"no keyring write" would pin the very gap being closed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import integrations.cli as cli
import integrations.telegram.verifier as telegram_verifier

_TOKEN = "123456789:AAExampleSecretTokenValue"
_CHAT_ID = "-1001234567890"
_CONNECTED = "Connected to Telegram bot @acme_bot."


def _prompts(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[tuple[str, bool]]:
    """Feed ``_p`` the given answers in prompt order; return the labels asked."""
    asked: list[tuple[str, bool]] = []
    queue = list(answers)

    def _fake_p(label: str, default: str = "", secret: bool = False) -> str:
        asked.append((label, secret))
        return queue.pop(0)

    monkeypatch.setattr(cli, "_p", _fake_p)
    return asked


def _verifier(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    detail: str = _CONNECTED,
) -> list[tuple[str, dict[str, Any]]]:
    """Stub the vendor verifier; return the calls it received."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def _fake_verify(source: str, config: dict[str, Any]) -> dict[str, str]:
        calls.append((source, dict(config)))
        return {"status": status, "detail": detail}

    # _setup_telegram imports the verifier inside the function body, so patching
    # the attribute on its owning module is what takes effect at call time.
    monkeypatch.setattr(telegram_verifier, "verify_telegram", _fake_verify)
    return calls


def _saves(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    saved: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        cli,
        "upsert_integration",
        lambda service, payload: saved.append((service, payload)),
    )
    return saved


def test_prompts_for_token_then_chat_id_and_saves_after_verifying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _prompts(monkeypatch, _TOKEN, _CHAT_ID)
    verified = _verifier(monkeypatch, "passed")
    saved = _saves(monkeypatch)

    cli._setup_telegram()

    # The token is collected as a secret; the chat id is not.
    assert [secret for _label, secret in asked] == [True, False]
    assert "token" in asked[0][0].lower()
    assert "chat" in asked[1][0].lower()

    assert verified == [("setup", {"bot_token": _TOKEN})]
    assert saved == [
        (
            "telegram",
            {"credentials": {"bot_token": _TOKEN, "default_chat_id": _CHAT_ID}},
        )
    ]


def test_blank_chat_id_is_stored_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A skipped chat id is persisted as ``None``, not an empty string."""
    _prompts(monkeypatch, _TOKEN, "")
    _verifier(monkeypatch, "passed")
    saved = _saves(monkeypatch)

    cli._setup_telegram()

    assert saved[0][1]["credentials"]["default_chat_id"] is None


def test_blank_token_exits_before_verifying_or_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prompts(monkeypatch, "")
    verified = _verifier(monkeypatch, "passed")
    saved = _saves(monkeypatch)

    with pytest.raises(SystemExit):
        cli._setup_telegram()

    assert verified == []
    assert saved == []


def test_failed_verification_exits_without_saving(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad token must not overwrite a working integration."""
    _prompts(monkeypatch, _TOKEN, _CHAT_ID)
    _verifier(monkeypatch, "failed", "Telegram API check failed: Unauthorized")
    saved = _saves(monkeypatch)

    with pytest.raises(SystemExit):
        cli._setup_telegram()

    assert saved == []


def test_success_reports_the_bot_and_the_verify_follow_up(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The bot label and the `integrations verify` next step are user-visible."""
    _prompts(monkeypatch, _TOKEN, _CHAT_ID)
    _verifier(monkeypatch, "passed")
    _saves(monkeypatch)

    cli._setup_telegram()

    out = capsys.readouterr().out
    assert "@acme_bot" in out
    assert "opensre integrations verify telegram" in out
    assert _TOKEN not in out


def test_setup_handler_is_registered_for_telegram() -> None:
    """The dispatch entry is what makes `integrations setup telegram` reachable."""
    handler: Callable[[], None] = cli._HANDLERS["telegram"]
    assert handler is cli._setup_telegram
