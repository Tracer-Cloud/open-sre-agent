"""Tier policy for secret storage: environment first, owner-only local file second.

The OS keychain is no longer a tier. Reading it raised an approval dialog on
macOS for every lookup, so onboarding asked permission on each launch. What
these tests pin is that the remaining path still reaches a working state on
every machine, and that logout genuinely revokes.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import keyring
import pytest

from config.constants.secrets import OPENSRE_DISABLE_KEYRING_ENV
from config.llm_auth.credentials import delete as delete_provider_auth
from config.llm_auth.credentials import resolve_for_request, save_api_key
from config.secrets import keychain_import, local_file, os_keyring
from config.secrets.backend import (
    KeyringUnavailableError,
    KeyringUnavailableReason,
    SecretTier,
)
from config.secrets.store import (
    delete_secret,
    lookup,
    resolve_secret,
    save_secret,
    secret_source,
)
from tests.shared.keyring_backend import MemoryKeyring

_ENV_VAR = "OPENSRE_TEST_FALLBACK_TOKEN"


@pytest.fixture(autouse=True)
def _local_storage_enabled(tmp_path: Path, monkeypatch) -> None:
    """Undo the suite-wide disable switch so the storage policy is exercised."""
    monkeypatch.delenv(OPENSRE_DISABLE_KEYRING_ENV, raising=False)
    monkeypatch.delenv(_ENV_VAR, raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    os_keyring.reset_keyring_state()
    keychain_import.reset_import_state()
    monkeypatch.setattr(local_file, "store_path", lambda: tmp_path / "credentials.json")
    # Do not migrate the developer's real keychain into the temp store.
    monkeypatch.setattr(keychain_import, "_already_imported", lambda: True)


def _stored_contents() -> dict[str, str]:
    path = local_file.store_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["secrets"]


def test_keyring_unavailable_reason_values_round_trip() -> None:
    """The reason enum still classifies why a *write* was refused."""
    expected = {
        "disabled": KeyringUnavailableReason.DISABLED,
        "no_backend": KeyringUnavailableReason.NO_BACKEND,
        "backend_error": KeyringUnavailableReason.BACKEND_ERROR,
    }

    assert {reason.value: reason for reason in KeyringUnavailableReason} == expected
    for value, reason in expected.items():
        assert KeyringUnavailableReason(value) == reason
        assert isinstance(reason, str)
        assert str(reason) == value


def test_a_saved_credential_is_resolvable_at_request_time() -> None:
    """A credential accepted during onboarding has to actually work afterwards."""
    save_secret(_ENV_VAR, "sk-headless")

    found = lookup(_ENV_VAR)

    assert found.value == "sk-headless"
    assert found.tier == "fallback"
    assert secret_source(_ENV_VAR) == "fallback"


def test_env_wins_over_the_stored_copy(monkeypatch) -> None:
    save_secret(_ENV_VAR, "sk-stored")
    monkeypatch.setenv(_ENV_VAR, "sk-from-env")

    found = lookup(_ENV_VAR)

    assert found.value == "sk-from-env"
    assert found.tier == "env"


def test_lookup_never_opens_the_os_keychain(monkeypatch) -> None:
    """The read path is what raised the macOS approval dialog on every launch."""

    # Arrange — finish the one-time import so lookup does not probe the keychain.
    monkeypatch.setattr(keychain_import, "_already_imported", lambda: True)

    def _fail(env_var: str) -> str:
        raise AssertionError(f"the keychain was read for {env_var}")

    monkeypatch.setattr(os_keyring, "get", _fail)
    monkeypatch.setattr(keychain_import.os_keyring, "get", _fail)
    save_secret(_ENV_VAR, "sk-local")

    # Act / Assert
    assert lookup(_ENV_VAR).value == "sk-local"
    assert lookup("OPENSRE_TEST_ABSENT_TOKEN").value == ""


def test_lookup_tolerates_a_credential_file_lock_timeout(monkeypatch) -> None:
    """Post-import local_file.get must not raise through resolve/startup."""
    from filelock import Timeout

    monkeypatch.setattr(keychain_import, "import_keychain_secrets_once", lambda: ())
    monkeypatch.setattr(keychain_import, "import_named_keychain_secret", lambda _name: "")

    def _locked(_name: str) -> str:
        raise Timeout("/tmp/credentials.json.lock")

    monkeypatch.setattr(local_file, "get", _locked)

    assert lookup(_ENV_VAR).value == ""
    assert lookup(_ENV_VAR).tier == SecretTier.NONE


def test_lookup_tolerates_local_store_error_that_is_not_an_oserror(monkeypatch) -> None:
    """LocalStoreError must be caught even though it does not subclass OSError."""
    assert not issubclass(local_file.LocalStoreError, OSError)

    monkeypatch.setattr(keychain_import, "import_keychain_secrets_once", lambda: ())
    monkeypatch.setattr(keychain_import, "import_named_keychain_secret", lambda _name: "")

    def _locked(_name: str) -> str:
        raise local_file.LocalStoreError("lock timed out")

    monkeypatch.setattr(local_file, "get", _locked)

    assert lookup(_ENV_VAR).value == ""
    assert lookup(_ENV_VAR).tier == SecretTier.NONE


def test_save_maps_a_lock_timeout_to_unavailable(monkeypatch) -> None:
    def _locked(*_args: object, **_kwargs: object) -> None:
        raise local_file.LocalStoreError("lock timed out")

    monkeypatch.setattr(local_file, "set", _locked)

    with pytest.raises(KeyringUnavailableError) as excinfo:
        save_secret(_ENV_VAR, "sk-headless")

    assert excinfo.value.reason == KeyringUnavailableReason.NO_BACKEND


def test_lookup_migrates_a_dynamic_keychain_name_on_demand(monkeypatch) -> None:
    """Non-enumerable leftovers become visible when something asks for that name."""
    dynamic = "MY_CUSTOM_INTEGRATION_TOKEN"
    entries = {dynamic: "sk-dynamic"}
    monkeypatch.setattr(keychain_import, "_already_imported", lambda: False)
    monkeypatch.setattr(keychain_import, "import_keychain_secrets_once", lambda: ())
    monkeypatch.setattr(os_keyring, "item_exists", lambda name: name in entries)
    monkeypatch.setattr(os_keyring, "get", lambda name: entries.get(name, ""))
    monkeypatch.setattr(os_keyring, "delete", lambda name: entries.pop(name, None))
    # import_named_keychain_secret uses keychain_import.os_keyring
    monkeypatch.setattr(keychain_import.os_keyring, "item_exists", lambda name: name in entries)
    monkeypatch.setattr(keychain_import.os_keyring, "get", lambda name: entries.get(name, ""))
    monkeypatch.setattr(keychain_import.os_keyring, "delete", lambda name: entries.pop(name, None))
    monkeypatch.setattr(keychain_import.os_keyring, "keyring_is_disabled", lambda: False)

    found = lookup(dynamic)

    assert found.value == "sk-dynamic"
    assert found.tier == SecretTier.FALLBACK
    assert local_file.get(dynamic) == "sk-dynamic"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_the_credential_file_is_never_readable_beyond_its_owner() -> None:
    save_secret(_ENV_VAR, "sk-headless")

    mode = stat.S_IMODE(local_file.store_path().stat().st_mode)

    assert mode == 0o600


def test_delete_clears_the_stored_copy(monkeypatch) -> None:
    """Logout must not leave a copy that keeps resolving."""
    # Isolate from a locked developer keychain; scrub success is covered elsewhere.
    monkeypatch.setattr(os_keyring, "delete", lambda _name: None)
    save_secret(_ENV_VAR, "sk-stored")

    delete_secret(_ENV_VAR)

    assert resolve_secret(_ENV_VAR) == ""
    assert _ENV_VAR not in _stored_contents()


def test_delete_also_scrubs_a_legacy_keychain_copy(monkeypatch) -> None:
    """Migrated leftovers in the OS keychain must not survive logout."""
    save_secret(_ENV_VAR, "sk-stored")
    deleted: list[str] = []
    monkeypatch.setattr(os_keyring, "delete", lambda name: deleted.append(name))

    delete_secret(_ENV_VAR)

    assert _ENV_VAR in deleted
    assert resolve_secret(_ENV_VAR) == ""


def test_delete_raises_when_the_keychain_scrub_fails(monkeypatch) -> None:
    """Logout must not report success while an OS copy remains recoverable."""
    save_secret(_ENV_VAR, "sk-stored")

    def _locked(_name: str) -> None:
        raise KeyringUnavailableError("locked", reason=KeyringUnavailableReason.BACKEND_ERROR)

    monkeypatch.setattr(os_keyring, "delete", _locked)

    with pytest.raises(KeyringUnavailableError) as excinfo:
        delete_secret(_ENV_VAR)

    assert excinfo.value.reason == KeyringUnavailableReason.BACKEND_ERROR
    assert _ENV_VAR not in _stored_contents()


def test_delete_raises_when_the_local_store_lock_times_out(monkeypatch) -> None:
    """Logout must not report success while the local credential still resolves."""
    save_secret(_ENV_VAR, "sk-stored")
    scrubbed: list[str] = []
    monkeypatch.setattr(os_keyring, "delete", lambda name: scrubbed.append(name))

    def _locked(_name: str) -> None:
        raise local_file.LocalStoreError("lock timed out")

    monkeypatch.setattr(local_file, "delete", _locked)

    with pytest.raises(KeyringUnavailableError) as excinfo:
        delete_secret(_ENV_VAR)

    assert excinfo.value.reason == KeyringUnavailableReason.BACKEND_ERROR
    assert _ENV_VAR in scrubbed
    assert _ENV_VAR in _stored_contents()


def test_provider_logout_clears_the_stored_copy(monkeypatch) -> None:
    """Regression: `opensre auth logout` reported success while the key still worked."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(os_keyring, "delete", lambda _name: None)
    save_api_key("deepseek", "sk-headless")
    assert resolve_for_request("deepseek").api_key == "sk-headless"

    delete_provider_auth("deepseek")

    assert resolve_for_request("deepseek").ok is False
    assert _stored_contents() == {}


def test_request_resolution_reports_the_stored_tier(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    save_api_key("deepseek", "sk-headless")

    resolution = resolve_for_request("deepseek")

    assert resolution.api_key == "sk-headless"
    assert resolution.source == "fallback"


def test_save_raises_when_the_file_refuses(monkeypatch) -> None:
    """A caller that sees no exception must be able to trust the write landed."""

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(local_file, "set", _refuse)

    with pytest.raises(KeyringUnavailableError) as excinfo:
        save_secret(_ENV_VAR, "sk-headless")

    assert excinfo.value.reason == KeyringUnavailableReason.NO_BACKEND


def test_disabling_local_storage_stays_fail_closed(monkeypatch) -> None:
    """OPENSRE_DISABLE_KEYRING opts out of persistence, not into a weaker tier."""
    monkeypatch.setenv(OPENSRE_DISABLE_KEYRING_ENV, "1")

    with pytest.raises(KeyringUnavailableError) as excinfo:
        save_secret(_ENV_VAR, "sk-disabled")

    assert excinfo.value.reason == KeyringUnavailableReason.DISABLED
    assert not Path(local_file.store_path()).exists()


def test_disabled_local_storage_reads_only_the_environment(monkeypatch) -> None:
    save_secret(_ENV_VAR, "sk-stored")
    monkeypatch.setenv(OPENSRE_DISABLE_KEYRING_ENV, "1")

    assert resolve_secret(_ENV_VAR) == ""

    monkeypatch.setenv(_ENV_VAR, "sk-from-env")
    assert resolve_secret(_ENV_VAR) == "sk-from-env"


def test_empty_value_clears_instead_of_storing(monkeypatch) -> None:
    monkeypatch.setattr(os_keyring, "delete", lambda _name: None)
    save_secret(_ENV_VAR, "sk-headless")

    save_secret(_ENV_VAR, "   ")

    assert resolve_secret(_ENV_VAR) == ""
    assert _ENV_VAR not in _stored_contents()


def test_logout_scrubs_the_keychain_even_when_local_storage_is_disabled(monkeypatch) -> None:
    """Revocation must reach the legacy keychain copy regardless of the switch.

    ``OPENSRE_DISABLE_KEYRING`` means "do not persist locally", not "leave a
    revoked credential recoverable". Returning early left the keychain entry
    intact while logout reported success, so unsetting the flag — or running an
    older release — restored access.
    """
    # Arrange — drive the real backend, not a stand-in for os_keyring.delete:
    # the disable guard sits between them and is what this pins.
    deleted: list[tuple[str, str]] = []

    class _RecordingKeyring(MemoryKeyring):
        def delete_password(self, service: str, username: str) -> None:
            deleted.append((service, username))

    previous = keyring.get_keyring()
    keyring.set_keyring(_RecordingKeyring())
    os_keyring.reset_keyring_state()
    monkeypatch.setenv(OPENSRE_DISABLE_KEYRING_ENV, "1")
    try:
        # Act
        delete_secret(_ENV_VAR)
    finally:
        keyring.set_keyring(previous)
        os_keyring.reset_keyring_state()

    # Assert
    assert deleted == [("opensre.llm", _ENV_VAR)]
