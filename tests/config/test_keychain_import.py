"""One-time keychain import: existing entries survive the move off the keyring.

Secrets saved by older versions live in the OS keychain. Reads no longer consult
it, so they are imported into the local credential file once; keychain copies
are scrubbed, and the marker is written only after a complete probe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants.secrets import OPENSRE_DISABLE_KEYRING_ENV
from config.secrets import keychain_import, local_file, os_keyring


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Suite conftest disables local persistence; this module must exercise it.
    monkeypatch.delenv(OPENSRE_DISABLE_KEYRING_ENV, raising=False)
    os_keyring.reset_keyring_state()
    monkeypatch.setattr(local_file, "store_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(keychain_import, "_marker_path", lambda: tmp_path / "keychain-imported")


def _install_keychain(monkeypatch: pytest.MonkeyPatch, entries: dict[str, str]) -> list[str]:
    """Install keychain fakes; return the list that records deletions."""
    deleted: list[str] = []

    def _item_exists(env_var: str) -> bool | None:
        return env_var in entries

    def _get(env_var: str) -> str:
        return entries.get(env_var, "")

    def _delete(env_var: str) -> None:
        deleted.append(env_var)
        entries.pop(env_var, None)

    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", _item_exists)
    monkeypatch.setattr(keychain_import.os_keyring, "get", _get)
    monkeypatch.setattr(keychain_import.os_keyring, "delete", _delete)
    return deleted


def test_existing_keychain_secrets_move_into_the_local_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    deleted = _install_keychain(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-live"})

    # Act
    imported = keychain_import.import_keychain_secrets_once()

    # Assert
    assert imported == ("ANTHROPIC_API_KEY",)
    assert local_file.get("ANTHROPIC_API_KEY") == "sk-ant-live"
    assert "ANTHROPIC_API_KEY" in deleted


def test_integration_secrets_are_migrated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram / Slack tokens share the keyring service and must move too."""
    deleted = _install_keychain(
        monkeypatch,
        {
            "TELEGRAM_BOT_TOKEN": "111:telegram",
            "SLACK_BOT_TOKEN": "xoxb-slack",
        },
    )

    imported = keychain_import.import_keychain_secrets_once()

    assert "TELEGRAM_BOT_TOKEN" in imported
    assert "SLACK_BOT_TOKEN" in imported
    assert local_file.get("TELEGRAM_BOT_TOKEN") == "111:telegram"
    assert local_file.get("SLACK_BOT_TOKEN") == "xoxb-slack"
    assert "TELEGRAM_BOT_TOKEN" in deleted
    assert "SLACK_BOT_TOKEN" in deleted


def test_the_import_runs_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second run must not touch the keychain again — that is the whole point."""
    # Arrange
    _install_keychain(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-live"})
    keychain_import.import_keychain_secrets_once()

    def _fail_if_read(_env_var: str) -> str:
        raise AssertionError("keychain was read after the one-time import")

    monkeypatch.setattr(keychain_import.os_keyring, "get", _fail_if_read)

    # Act / Assert
    assert keychain_import.import_keychain_secrets_once() == ()


def test_absent_entries_are_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probing costs no prompt; reading does. Only real items may be read."""
    # Arrange
    read_attempts: list[str] = []

    def _item_exists(_env_var: str) -> bool | None:
        return False

    def _get(env_var: str) -> str:
        read_attempts.append(env_var)
        return ""

    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", _item_exists)
    monkeypatch.setattr(keychain_import.os_keyring, "get", _get)

    # Act
    keychain_import.import_keychain_secrets_once()

    # Assert
    assert read_attempts == []


def test_indeterminate_existence_still_attempts_a_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``item_exists is None`` must not skip the secret — that stranded Linux installs."""

    def _item_exists(_env_var: str) -> bool | None:
        return None

    def _get(env_var: str) -> str:
        return "sk-from-get" if env_var == "ANTHROPIC_API_KEY" else ""

    deleted: list[str] = []
    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", _item_exists)
    monkeypatch.setattr(keychain_import.os_keyring, "get", _get)
    monkeypatch.setattr(keychain_import.os_keyring, "delete", lambda name: deleted.append(name))

    imported = keychain_import.import_keychain_secrets_once()

    assert "ANTHROPIC_API_KEY" in imported
    assert local_file.get("ANTHROPIC_API_KEY") == "sk-from-get"


def test_a_value_already_in_the_local_file_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local file is authoritative — the keychain copy may be stale."""
    # Arrange
    local_file.set("ANTHROPIC_API_KEY", "sk-ant-current")
    deleted = _install_keychain(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-stale"})

    # Act
    keychain_import.import_keychain_secrets_once()

    # Assert
    assert local_file.get("ANTHROPIC_API_KEY") == "sk-ant-current"
    assert "ANTHROPIC_API_KEY" in deleted


def test_an_unreachable_keychain_does_not_block_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A locked or missing keychain must not raise into the boot path."""

    # Arrange
    def _boom(_env_var: str) -> bool | None:
        raise OSError("keychain locked")

    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", _boom)

    # Act / Assert
    assert keychain_import.import_keychain_secrets_once() == ()


def test_a_locked_keychain_leaves_the_import_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed read must not mark the import done — the secret is still there.

    Marking unconditionally turned one locked keychain at startup into a
    permanent skip, so the key could never be recovered.
    """

    # Arrange
    def _locked(_env_var: str) -> bool | None:
        raise OSError("keychain locked")

    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", _locked)
    assert keychain_import.import_keychain_secrets_once() == ()

    # Act — the keychain is available on the next run.
    _install_keychain(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-live"})

    # Assert
    assert keychain_import.import_keychain_secrets_once() == ("ANTHROPIC_API_KEY",)
    assert local_file.get("ANTHROPIC_API_KEY") == "sk-ant-live"
