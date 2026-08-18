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
from config.secrets.backend import KeyringUnavailableError, KeyringUnavailableReason


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Suite conftest disables local persistence; this module must exercise it.
    monkeypatch.delenv(OPENSRE_DISABLE_KEYRING_ENV, raising=False)
    os_keyring.reset_keyring_state()
    keychain_import.reset_import_state()
    monkeypatch.setattr(local_file, "store_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(keychain_import, "_marker_path", lambda: tmp_path / "keychain-imported")
    # Tests that need enumeration opt in; default off so CI never dumps the
    # developer keychain and so non-macOS runners stay deterministic.
    monkeypatch.setattr(keychain_import.os_keyring, "supports_username_enumeration", lambda: False)
    monkeypatch.setattr(keychain_import.os_keyring, "list_usernames", lambda: None)


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
    """Integration secrets share the keyring service and must move too.

    Covers barrel-exported names (Telegram/Slack), constants that live only in
    a constants submodule (Discord), and catalog string-literal secrets
    (Airflow/Trello) registered under ``config.constants.integration_secrets``.
    """
    deleted = _install_keychain(
        monkeypatch,
        {
            "TELEGRAM_BOT_TOKEN": "111:telegram",
            "SLACK_BOT_TOKEN": "xoxb-slack",
            "DISCORD_BOT_TOKEN": "discord-bot",
            "AIRFLOW_PASSWORD": "airflow-secret",
            "TRELLO_TOKEN": "trello-token",
            "GITLAB_ACCESS_TOKEN": "glpat-live",
            "DD_API_KEY": "dd-api",
        },
    )

    imported = keychain_import.import_keychain_secrets_once()

    for name, value in (
        ("TELEGRAM_BOT_TOKEN", "111:telegram"),
        ("SLACK_BOT_TOKEN", "xoxb-slack"),
        ("DISCORD_BOT_TOKEN", "discord-bot"),
        ("AIRFLOW_PASSWORD", "airflow-secret"),
        ("TRELLO_TOKEN", "trello-token"),
        ("GITLAB_ACCESS_TOKEN", "glpat-live"),
        ("DD_API_KEY", "dd-api"),
    ):
        assert name in imported
        assert local_file.get(name) == value
        assert name in deleted


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


def test_a_local_file_lock_timeout_leaves_the_import_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A contended credential file must not raise through lookup/startup."""
    from filelock import Timeout

    _install_keychain(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-live"})

    def _locked(_name: str) -> str:
        raise Timeout("/tmp/credentials.json.lock")

    monkeypatch.setattr(local_file, "get", _locked)

    assert keychain_import.import_keychain_secrets_once() == ()
    assert keychain_import._already_imported() is False


def test_import_tolerates_local_store_error_that_is_not_an_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LocalStoreError must be caught even though it does not subclass OSError."""
    assert not issubclass(local_file.LocalStoreError, OSError)

    _install_keychain(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-live"})

    def _locked(_name: str) -> str:
        raise local_file.LocalStoreError("lock timed out")

    monkeypatch.setattr(local_file, "get", _locked)

    assert keychain_import.import_keychain_secrets_once() == ()
    assert keychain_import._already_imported() is False


def test_a_failed_local_keys_listing_leaves_the_import_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock timeout on keys() must not finalize — dynamic local twins go unscanned."""
    local_file.set("MY_CUSTOM_INTEGRATION_TOKEN", "sk-local")
    deleted = _install_keychain(monkeypatch, {"MY_CUSTOM_INTEGRATION_TOKEN": "sk-stale"})

    def _locked_keys() -> tuple[str, ...]:
        raise local_file.LocalStoreError("lock timed out")

    monkeypatch.setattr(local_file, "keys", _locked_keys)

    keychain_import.import_keychain_secrets_once()

    assert keychain_import._already_imported() is False
    assert "MY_CUSTOM_INTEGRATION_TOKEN" not in deleted


def test_filelock_timeout_is_translated_before_leaving_local_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """filelock.Timeout must not escape get/keys — callers see LocalStoreError."""
    from filelock import FileLock

    store = tmp_path / "credentials.json"
    store.write_text('{"version": 1, "secrets": {"X": "y"}}\n', encoding="utf-8")
    monkeypatch.setattr(local_file, "store_path", lambda: store)
    monkeypatch.setattr(local_file, "_LOCK_TIMEOUT_SECONDS", 0.05)

    with FileLock(str(store) + ".lock"):
        with pytest.raises(local_file.LocalStoreError):
            local_file.get("X")
        with pytest.raises(local_file.LocalStoreError):
            local_file.keys()


def test_non_enumerable_platform_never_writes_the_permanent_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without username listing, finishing the constants scan is not completion."""
    _install_keychain(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-live"})

    assert keychain_import.import_keychain_secrets_once() == ("ANTHROPIC_API_KEY",)
    assert local_file.get("ANTHROPIC_API_KEY") == "sk-ant-live"
    assert keychain_import._already_imported() is False


def test_a_failed_scrub_leaves_the_import_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local copy alone must not complete migration while the OS entry remains."""

    entries = {"ANTHROPIC_API_KEY": "sk-ant-live"}
    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", lambda name: name in entries)
    monkeypatch.setattr(keychain_import.os_keyring, "get", lambda name: entries.get(name, ""))

    def _locked_delete(_name: str) -> None:
        raise KeyringUnavailableError("locked", reason=KeyringUnavailableReason.BACKEND_ERROR)

    monkeypatch.setattr(keychain_import.os_keyring, "delete", _locked_delete)

    assert keychain_import.import_keychain_secrets_once() == ("ANTHROPIC_API_KEY",)
    assert local_file.get("ANTHROPIC_API_KEY") == "sk-ant-live"
    # Marker must stay absent so a later run retries the scrub.
    assert keychain_import._already_imported() is False

    deleted: list[str] = []
    monkeypatch.setattr(keychain_import.os_keyring, "delete", lambda name: deleted.append(name))
    assert keychain_import.import_keychain_secrets_once() == ()
    assert "ANTHROPIC_API_KEY" in deleted
    assert keychain_import._already_imported() is False


def test_enumerated_dynamic_names_are_migrated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Names the constants scan never knew must still move when the keychain lists them."""
    dynamic = "record:custom-integration-token"
    deleted = _install_keychain(monkeypatch, {dynamic: "secret-from-keychain"})
    monkeypatch.setattr(keychain_import.os_keyring, "supports_username_enumeration", lambda: True)
    monkeypatch.setattr(keychain_import.os_keyring, "list_usernames", lambda: (dynamic,))

    imported = keychain_import.import_keychain_secrets_once()

    assert dynamic in imported
    assert local_file.get(dynamic) == "secret-from-keychain"
    assert dynamic in deleted
    assert keychain_import._already_imported() is True


def test_failed_username_enumeration_leaves_the_import_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On enumerable platforms, a failed dump must not write the completion marker."""
    _install_keychain(monkeypatch, {"ANTHROPIC_API_KEY": "sk-ant-live"})
    monkeypatch.setattr(keychain_import.os_keyring, "supports_username_enumeration", lambda: True)
    monkeypatch.setattr(keychain_import.os_keyring, "list_usernames", lambda: None)

    imported = keychain_import.import_keychain_secrets_once()

    assert "ANTHROPIC_API_KEY" in imported
    assert keychain_import._already_imported() is False


def test_local_file_keys_are_scrubbed_even_when_not_in_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leftover keychain duplicate of a dynamic local name must still be deleted."""
    dynamic = "MY_CUSTOM_INTEGRATION_TOKEN"
    local_file.set(dynamic, "sk-local")
    deleted = _install_keychain(monkeypatch, {dynamic: "sk-stale-keychain"})

    keychain_import.import_keychain_secrets_once()

    assert local_file.get(dynamic) == "sk-local"
    assert dynamic in deleted


def test_dump_parser_extracts_accounts_for_our_service() -> None:
    dump = """
keychain: "/Users/me/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="ANTHROPIC_API_KEY"
    "svce"<blob>="opensre.llm"
keychain: "/Users/me/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="record:provider-auth:deepseek"
    "svce"<blob>="opensre.llm"
keychain: "/Users/me/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="Chrome"
    "svce"<blob>="Chrome Safe Storage"
"""
    assert os_keyring._usernames_for_service(dump, "opensre.llm") == (
        "ANTHROPIC_API_KEY",
        "record:provider-auth:deepseek",
    )


def test_a_failed_scrub_is_retried_on_a_later_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keychain copy must not survive because the local copy now shadows it.

    On-demand migration copies the value locally and then deletes the keychain
    entry. When that delete fails, later lookups resolve the local copy and never
    reach the on-demand path again, so the revoked-tier copy stayed recoverable.
    """
    # Arrange — the keychain holds the value; deleting it fails the first time.
    scrub_attempts: list[str] = []
    deleted: list[str] = []

    def _delete(env_var: str) -> None:
        scrub_attempts.append(env_var)
        if len(scrub_attempts) == 1:
            raise KeyringUnavailableError("locked", reason=KeyringUnavailableReason.BACKEND_ERROR)
        deleted.append(env_var)

    target = "MY_DYNAMIC_API_KEY"
    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", lambda n: n == target)
    monkeypatch.setattr(
        keychain_import.os_keyring, "get", lambda n: "sk-live" if n == target else ""
    )
    monkeypatch.setattr(keychain_import.os_keyring, "delete", _delete)

    # A clean known-name pass has already run in this process.
    keychain_import.import_keychain_secrets_once()

    # Act — on-demand migration copies the value but cannot scrub.
    assert keychain_import.import_named_keychain_secret("MY_DYNAMIC_API_KEY") == "sk-live"
    assert local_file.get("MY_DYNAMIC_API_KEY") == "sk-live"
    assert deleted == []

    # A completed known-name pass must not close the door on the pending scrub.
    keychain_import.import_keychain_secrets_once()

    # Assert
    assert deleted == ["MY_DYNAMIC_API_KEY"]
