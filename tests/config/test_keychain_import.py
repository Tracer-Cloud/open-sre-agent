"""One-time keychain import: existing entries survive the move off the keyring.

Secrets saved by older versions live in the OS keychain. Reads no longer consult
it, so they are imported into the local credential file once and the keychain is
never touched again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.secrets import keychain_import, local_file


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_file, "store_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(keychain_import, "_marker_path", lambda: tmp_path / "keychain-imported")


def _keychain(entries: dict[str, str]) -> tuple[object, object]:
    """Return ``(item_exists, get)`` fakes over ``entries``."""

    def _item_exists(env_var: str) -> bool | None:
        return env_var in entries

    def _get(env_var: str) -> str:
        return entries.get(env_var, "")

    return _item_exists, _get


def test_existing_keychain_secrets_move_into_the_local_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    item_exists, get = _keychain({"ANTHROPIC_API_KEY": "sk-ant-live"})
    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", item_exists)
    monkeypatch.setattr(keychain_import.os_keyring, "get", get)

    # Act
    imported = keychain_import.import_keychain_secrets_once()

    # Assert
    assert imported == ("ANTHROPIC_API_KEY",)
    assert local_file.get("ANTHROPIC_API_KEY") == "sk-ant-live"


def test_the_import_runs_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second run must not touch the keychain again — that is the whole point."""
    # Arrange
    item_exists, get = _keychain({"ANTHROPIC_API_KEY": "sk-ant-live"})
    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", item_exists)
    monkeypatch.setattr(keychain_import.os_keyring, "get", get)
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


def test_a_value_already_in_the_local_file_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local file is authoritative — the keychain copy may be stale."""
    # Arrange
    local_file.set("ANTHROPIC_API_KEY", "sk-ant-current")
    item_exists, get = _keychain({"ANTHROPIC_API_KEY": "sk-ant-stale"})
    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", item_exists)
    monkeypatch.setattr(keychain_import.os_keyring, "get", get)

    # Act
    keychain_import.import_keychain_secrets_once()

    # Assert
    assert local_file.get("ANTHROPIC_API_KEY") == "sk-ant-current"


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
    item_exists, get = _keychain({"ANTHROPIC_API_KEY": "sk-ant-live"})
    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", item_exists)
    monkeypatch.setattr(keychain_import.os_keyring, "get", get)

    # Assert
    assert keychain_import.import_keychain_secrets_once() == ("ANTHROPIC_API_KEY",)
    assert local_file.get("ANTHROPIC_API_KEY") == "sk-ant-live"
