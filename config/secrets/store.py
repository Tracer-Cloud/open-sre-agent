"""The one place that decides where an OpenSRE secret is read from and written to.

Every surface that persists or resolves a credential goes through here, so the
tier order is stated once instead of being re-derived at each call site:

    read   env  ->  OS keyring  ->  local fallback file
    write  OS keyring, falling back to the local file only when the keyring
           genuinely cannot store it
    delete both tiers, always

The delete rule is not symmetry for its own sake: clearing only the keyring
would leave a fallback copy that the read path happily resolves, so ``opensre
auth logout`` would report success while the credential kept working.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass

from config.constants.secrets import OPENSRE_DISABLE_CREDENTIAL_FALLBACK_ENV
from config.secrets import local_file, os_keyring
from config.secrets.backend import (
    KeyringUnavailableError,
    KeyringUnavailableReason,
    SecretTier,
)

_DISABLED_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class SecretLookup:
    """Where a secret resolved from, and why the keyring did not answer."""

    value: str
    tier: SecretTier
    keyring_error: str = ""

    @property
    def keyring_unreachable(self) -> bool:
        """True when the keychain could not be consulted at all.

        Distinct from "the keychain has no such entry": a caller must not mark a
        previously verified credential stale just because the backend was down.
        """
        return bool(self.keyring_error)


@dataclass(frozen=True)
class SecretSaveResult:
    """Which tier accepted the write."""

    tier: SecretTier
    detail: str = ""

    @property
    def used_fallback(self) -> bool:
        return self.tier == "fallback"


def fallback_is_disabled() -> bool:
    return (
        os.getenv(OPENSRE_DISABLE_CREDENTIAL_FALLBACK_ENV, "").strip().lower() in _DISABLED_VALUES
    )


def _fallback_refused_message(env_var: str, exc: KeyringUnavailableError) -> str:
    return (
        f"{exc} Refusing to store {env_var} in the local fallback file because "
        f"{OPENSRE_DISABLE_CREDENTIAL_FALLBACK_ENV} is set; export {env_var} in your "
        "shell instead."
    )


def lookup(env_var: str, *, default: str = "") -> SecretLookup:
    """Resolve a secret through env, then the keyring, then the fallback file."""
    env_value = os.getenv(env_var, default).strip()
    if env_value:
        return SecretLookup(env_value, "env")

    # The disable switch takes the machine out of local persistence entirely,
    # so neither tier is consulted and env vars are the only source.
    if os_keyring.keyring_is_disabled():
        return SecretLookup("", "none")

    keyring_error = ""
    try:
        value = os_keyring.get(env_var)
    except KeyringUnavailableError as exc:
        keyring_error = str(exc)
    else:
        if value:
            return SecretLookup(value, "keyring")

    if fallback_is_disabled():
        return SecretLookup("", "none", keyring_error)

    fallback_value = local_file.get(env_var)
    if fallback_value:
        return SecretLookup(fallback_value, "fallback", keyring_error)
    return SecretLookup("", "none", keyring_error)


def resolve_secret(env_var: str, *, default: str = "") -> str:
    """Resolve a secret, or ``""`` when no tier has it."""
    return lookup(env_var, default=default).value


def secret_source(env_var: str) -> SecretTier:
    """Which tier would serve this secret, without exposing its value."""
    return lookup(env_var).tier


def save_secret(env_var: str, value: str) -> SecretSaveResult:
    """Persist a secret, falling back to the local file when the keyring cannot.

    Raises :class:`KeyringUnavailableError` only when *no* tier accepted the
    write, so a caller that sees no exception knows the credential is durable.
    """
    normalized = value.strip()
    if not normalized:
        delete_secret(env_var)
        return SecretSaveResult("none", f"{env_var} cleared.")

    try:
        os_keyring.set(env_var, normalized)
    except KeyringUnavailableError as exc:
        # A machine that was told not to use its keychain is not asking for a
        # weaker store; it is asking for none.
        if exc.reason is KeyringUnavailableReason.DISABLED:
            raise
        if fallback_is_disabled():
            raise KeyringUnavailableError(
                _fallback_refused_message(env_var, exc), reason=exc.reason
            ) from exc
        try:
            local_file.set(env_var, normalized)
        except OSError as file_exc:
            raise KeyringUnavailableError(
                f"{exc} Writing {env_var} to {local_file.store_path()} also failed.",
                reason=exc.reason,
            ) from file_exc
        return SecretSaveResult("fallback", _fallback_detail(exc))

    # A keyring write supersedes any fallback copy from an earlier run, which
    # would otherwise shadow nothing but linger as a stale plaintext secret.
    if not fallback_is_disabled():
        _discard_fallback(env_var)
    return SecretSaveResult("keyring", f"{env_var} stored in the system keychain.")


def _fallback_detail(exc: KeyringUnavailableError) -> str:
    path = local_file.store_path()
    if exc.reason is KeyringUnavailableReason.NO_BACKEND:
        return (
            f"No system keychain is available on this machine, so the credential was "
            f"saved to {path} with owner-only permissions."
        )
    return (
        f"The system keychain ({os_keyring.backend_name()}) could not be reached, so the "
        f"credential was saved to {path} with owner-only permissions. Unlock the keychain "
        "and re-run this command to move it back into secure storage."
    )


def _discard_fallback(env_var: str) -> None:
    # A stale fallback entry we could not clean up is a nuisance, not a reason
    # to fail a write that already succeeded in the keychain.
    with suppress(OSError):
        local_file.delete(env_var)


def delete_secret(env_var: str) -> None:
    """Remove a secret from both tiers.

    Never raises for an absent entry or an unreachable keyring — logout must not
    fail because the tier it was clearing was already gone.
    """
    if not os_keyring.keyring_is_disabled():
        with suppress(KeyringUnavailableError):
            os_keyring.delete(env_var)
    # Cleared unconditionally: a fallback copy written before the disable switch
    # was set would otherwise survive a logout that reported success.
    with suppress(OSError):
        local_file.delete(env_var)


def keyring_is_disabled() -> bool:
    """Whether ``OPENSRE_DISABLE_KEYRING`` takes this machine out of local storage."""
    return os_keyring.keyring_is_disabled()


__all__ = [
    "SecretLookup",
    "SecretSaveResult",
    "delete_secret",
    "fallback_is_disabled",
    "keyring_is_disabled",
    "lookup",
    "resolve_secret",
    "save_secret",
    "secret_source",
]
