"""Tier policy for secret storage: keyring first, owner-only local file second.

Covers the machines the OS keyring does not serve — headless Linux/EC2 with no
backend at all, and a macOS login Keychain that is present but locked — where
onboarding previously had no path to a working state.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import keyring
import keyring.errors
import pytest

from config.constants.secrets import OPENSRE_DISABLE_KEYRING_ENV, OPENSRE_USE_KEYRING_ENV
from config.llm_auth.credentials import delete as delete_provider_auth
from config.llm_auth.credentials import resolve_for_request, save_api_key
from config.secrets import local_file, os_keyring
from config.secrets.backend import KeyringUnavailableError, KeyringUnavailableReason
from config.secrets.store import (
    delete_secret,
    lookup,
    resolve_secret,
    save_secret,
    secret_source,
)
from tests.shared.keyring_backend import MemoryKeyring

_ENV_VAR = "OPENSRE_TEST_FALLBACK_TOKEN"


def test_keyring_unavailable_reason_values_round_trip() -> None:
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


class _NoBackendKeyring(MemoryKeyring):
    """Stands in for ``keyring.backends.fail.Keyring`` on a headless box."""

    def get_password(self, _service: str, _username: str) -> str | None:
        raise keyring.errors.NoKeyringError("No recommended backend was available")

    def set_password(self, _service: str, _username: str, _password: str) -> None:
        raise keyring.errors.NoKeyringError("No recommended backend was available")

    def delete_password(self, _service: str, _username: str) -> None:
        raise keyring.errors.NoKeyringError("No recommended backend was available")


class _LockedKeyring(MemoryKeyring):
    """A real backend that is present but refuses to serve (locked Keychain)."""

    def get_password(self, _service: str, _username: str) -> str | None:
        raise keyring.errors.KeyringLocked("The keychain is locked")

    def set_password(self, _service: str, _username: str, _password: str) -> None:
        raise keyring.errors.KeyringLocked("The keychain is locked")

    def delete_password(self, _service: str, _username: str) -> None:
        raise keyring.errors.KeyringLocked("The keychain is locked")


@pytest.fixture
def keyring_enabled(monkeypatch) -> None:
    """Undo the suite-wide disable switch so tier policy is actually exercised.

    Also opts into keyring *writes* (``OPENSRE_USE_KEYRING``) — the product
    default is file-first; these tests pin the opt-in keychain path.
    """
    monkeypatch.delenv(OPENSRE_DISABLE_KEYRING_ENV, raising=False)
    monkeypatch.setenv(OPENSRE_USE_KEYRING_ENV, "1")
    monkeypatch.delenv(_ENV_VAR, raising=False)
    os_keyring.reset_keyring_state()


@pytest.fixture
def use_backend(keyring_enabled):
    """Install a keyring backend for one test, restoring the real one after."""
    previous = keyring.get_keyring()

    def _install(backend: keyring.backend.KeyringBackend) -> None:
        keyring.set_keyring(backend)
        os_keyring.reset_keyring_state()

    yield _install
    keyring.set_keyring(previous)
    os_keyring.reset_keyring_state()


def _fallback_contents() -> dict[str, str]:
    path = local_file.store_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["secrets"]


def test_save_defaults_to_fallback_file_without_keyring_opt_in(monkeypatch, use_backend) -> None:
    """Env/file-first: keyring writes require OPENSRE_USE_KEYRING=1."""
    use_backend(MemoryKeyring())
    monkeypatch.delenv(OPENSRE_USE_KEYRING_ENV, raising=False)

    result = save_secret(_ENV_VAR, "sk-file-first")

    assert result.tier == "fallback"
    assert "OPENSRE_USE_KEYRING=1" in result.detail
    assert _fallback_contents() == {_ENV_VAR: "sk-file-first"}
    from config.constants.secrets import KEYRING_SERVICE

    assert keyring.get_password(KEYRING_SERVICE, _ENV_VAR) in (None, "")


def test_save_falls_back_to_local_file_when_no_backend_exists(use_backend) -> None:
    """#1403: headless Linux/EC2 must reach a working state, not abort onboarding."""
    use_backend(_NoBackendKeyring())

    result = save_secret(_ENV_VAR, "sk-headless")

    assert result.tier == "fallback"
    assert result.used_fallback is True
    assert _fallback_contents() == {_ENV_VAR: "sk-headless"}


def test_save_falls_back_when_the_keychain_is_locked(use_backend) -> None:
    """#3348: a locked macOS login Keychain is a backend error, not a missing backend."""
    use_backend(_LockedKeyring())

    result = save_secret(_ENV_VAR, "sk-locked")

    assert result.tier == "fallback"
    # The user is told a real keychain exists and how to get back to it, rather
    # than being silently downgraded to on-disk storage.
    assert "could not be reached" in result.detail
    assert "Unlock the keychain" in result.detail


def test_fallback_credential_is_resolvable_at_request_time(use_backend) -> None:
    """A credential accepted during onboarding has to actually work afterwards."""
    use_backend(_NoBackendKeyring())
    save_secret(_ENV_VAR, "sk-headless")

    found = lookup(_ENV_VAR)

    assert found.value == "sk-headless"
    assert found.tier == "fallback"
    assert secret_source(_ENV_VAR) == "fallback"


def test_env_wins_over_both_stored_tiers(use_backend, monkeypatch) -> None:
    use_backend(_NoBackendKeyring())
    save_secret(_ENV_VAR, "sk-stored")
    monkeypatch.setenv(_ENV_VAR, "sk-from-env")

    found = lookup(_ENV_VAR)

    assert found.value == "sk-from-env"
    assert found.tier == "env"


def test_keyring_wins_over_a_stale_fallback_copy(use_backend) -> None:
    use_backend(_NoBackendKeyring())
    save_secret(_ENV_VAR, "sk-old-fallback")

    use_backend(MemoryKeyring())
    result = save_secret(_ENV_VAR, "sk-new-keyring")

    assert result.tier == "keyring"
    assert lookup(_ENV_VAR).tier == "keyring"
    # The superseded plaintext copy is removed rather than left lying around.
    assert _ENV_VAR not in _fallback_contents()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_fallback_file_is_never_readable_beyond_its_owner(use_backend) -> None:
    use_backend(_NoBackendKeyring())

    save_secret(_ENV_VAR, "sk-headless")

    mode = stat.S_IMODE(local_file.store_path().stat().st_mode)
    assert mode == 0o600


def test_delete_clears_both_tiers(use_backend) -> None:
    """Logout must not leave a fallback copy that keeps resolving."""
    backend = MemoryKeyring()
    use_backend(backend)
    save_secret(_ENV_VAR, "sk-keyring")
    # Simulate a fallback copy written by an earlier run on a broken machine.
    local_file.set(_ENV_VAR, "sk-fallback")

    delete_secret(_ENV_VAR)

    assert resolve_secret(_ENV_VAR) == ""
    assert _ENV_VAR not in _fallback_contents()
    assert backend.get_password("opensre.llm", _ENV_VAR) is None


def test_provider_logout_clears_the_fallback_copy(use_backend, monkeypatch) -> None:
    """Regression: `opensre auth logout` reported success while the key still worked."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    use_backend(_NoBackendKeyring())
    save_api_key("deepseek", "sk-headless")
    assert resolve_for_request("deepseek").api_key == "sk-headless"

    delete_provider_auth("deepseek")

    assert resolve_for_request("deepseek").ok is False
    assert _fallback_contents() == {}


def test_request_resolution_reports_the_fallback_tier(use_backend, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    use_backend(_NoBackendKeyring())
    save_api_key("deepseek", "sk-headless")

    resolution = resolve_for_request("deepseek")

    assert resolution.api_key == "sk-headless"
    assert resolution.source == "fallback"


def test_save_raises_when_both_tiers_refuse(use_backend, monkeypatch) -> None:
    use_backend(_NoBackendKeyring())

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(local_file, "set", _refuse)

    with pytest.raises(KeyringUnavailableError) as excinfo:
        save_secret(_ENV_VAR, "sk-headless")

    assert "also failed" in str(excinfo.value)


def test_disabling_the_keyring_stays_fail_closed(monkeypatch) -> None:
    """OPENSRE_DISABLE_KEYRING opts out of local persistence, not into a weaker tier."""
    monkeypatch.setenv(OPENSRE_DISABLE_KEYRING_ENV, "1")

    with pytest.raises(KeyringUnavailableError) as excinfo:
        save_secret(_ENV_VAR, "sk-disabled")

    assert excinfo.value.reason == KeyringUnavailableReason.DISABLED
    assert not Path(local_file.store_path()).exists()


def test_one_backend_probe_per_process_after_a_failure(use_backend) -> None:
    """#3295: repeated reads must not re-hit a backend that already failed.

    On macOS each hit is another Keychain authorization dialog; on a broken
    D-Bus box each one costs a timeout.
    """
    calls = 0

    class _CountingKeyring(MemoryKeyring):
        def get_password(self, _service: str, _username: str) -> str | None:
            nonlocal calls
            calls += 1
            raise keyring.errors.KeyringLocked("The keychain is locked")

    use_backend(_CountingKeyring())

    for _ in range(5):
        lookup(_ENV_VAR)

    assert calls == 1


def test_empty_value_clears_instead_of_storing(use_backend) -> None:
    use_backend(_NoBackendKeyring())
    save_secret(_ENV_VAR, "sk-headless")

    save_secret(_ENV_VAR, "   ")

    assert resolve_secret(_ENV_VAR) == ""
    assert _ENV_VAR not in _fallback_contents()


def test_a_failed_delete_does_not_lock_the_process_out_of_the_keyring(use_backend) -> None:
    """A logout that could not reach the backend must not demote later writes.

    ``delete_secret`` already treats a failed keyring delete as non-fatal, so if
    it also set the sticky unavailable flag, every subsequent read and write in
    the run would skip the keychain and use the plaintext fallback instead.
    """
    delete_calls = 0

    class _DeleteFailsOnceKeyring(MemoryKeyring):
        def delete_password(self, _service: str, _username: str) -> None:
            nonlocal delete_calls
            delete_calls += 1
            raise keyring.errors.KeyringLocked("The keychain is locked")

    use_backend(_DeleteFailsOnceKeyring())

    delete_secret(_ENV_VAR)
    result = save_secret(_ENV_VAR, "sk-after-failed-delete")

    assert delete_calls == 1
    assert result.tier == "keyring"
    assert lookup(_ENV_VAR).tier == "keyring"
