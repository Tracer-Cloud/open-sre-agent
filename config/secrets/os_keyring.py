"""The OS keyring tier, with every backend failure normalized to one error.

``keyring`` backends disagree about how they fail: macOS raises
``KeyringLocked``, SecretService can raise a bare ``RuntimeError`` when D-Bus is
unset, a read-only home raises ``OSError``, and a machine with no backend at all
resolves to ``keyring.backends.fail.Keyring`` and raises ``NoKeyringError``.
Callers must not have to know that list — everything here surfaces as
:class:`~config.secrets.backend.KeyringUnavailableError`, so the fallback policy
in :mod:`config.secrets.store` has exactly one condition to test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import keyring
import keyring.errors

from config.constants.secrets import (
    KEYRING_SERVICE,
    OPENSRE_DISABLE_KEYRING_ENV,
    OPENSRE_USE_KEYRING_ENV,
)
from config.secrets.backend import KeyringUnavailableError, KeyringUnavailableReason

_DISABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_MACOS_PROBE_TIMEOUT_SECONDS = 2.0
_MACOS_ITEM_NOT_FOUND_RETURNCODE = 44

# Sticky per-process record of the first hard failure. A machine without a
# working backend is not going to grow one mid-run, and on a broken D-Bus box
# each attempt costs a timeout rather than failing fast, so one probe decides
# for the whole process. Reset between tests via ``reset_keyring_state``.
_unavailable: KeyringUnavailableError | None = None


def reset_keyring_state() -> None:
    """Forget the sticky unavailability flag (test hook)."""
    global _unavailable
    _unavailable = None


def keyring_is_disabled() -> bool:
    return os.getenv(OPENSRE_DISABLE_KEYRING_ENV, "").strip().lower() in _DISABLED_VALUES


def keyring_writes_enabled() -> bool:
    """Whether new secrets may be written to the OS keyring.

    Default is off (env-file / fallback-file first). Set ``OPENSRE_USE_KEYRING=1``
    to opt into keychain writes. Disable still wins over opt-in.
    """
    if keyring_is_disabled():
        return False
    return os.getenv(OPENSRE_USE_KEYRING_ENV, "").strip().lower() in _DISABLED_VALUES


def _backend() -> object:
    return keyring.get_keyring()


def backend_name() -> str:
    """Dotted class name of the resolved backend, for diagnostics."""
    try:
        backend = _backend()
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return "unknown"
    return f"{backend.__class__.__module__}.{backend.__class__.__name__}"


def _is_fail_backend() -> bool:
    try:
        backend = _backend()
    except Exception:  # noqa: BLE001 - treat an unusable resolver as no backend
        return True
    return backend.__class__.__module__.startswith("keyring.backends.fail")


def _is_macos_backend() -> bool:
    try:
        backend = _backend()
    except Exception:  # noqa: BLE001
        return False
    return backend.__class__.__module__.startswith("keyring.backends.macOS")


def _classify(exc: BaseException) -> KeyringUnavailableError:
    """Turn a backend exception into the one error type callers handle."""
    if isinstance(exc, keyring.errors.NoKeyringError) or _is_fail_backend():
        reason = KeyringUnavailableReason.NO_BACKEND
        message = "No system keychain backend is available on this machine."
    else:
        reason = KeyringUnavailableReason.BACKEND_ERROR
        message = f"The system keychain ({backend_name()}) is installed but could not be reached."
    return KeyringUnavailableError(message, reason=reason)


def _unavailable_error(exc: BaseException) -> KeyringUnavailableError:
    """Classify a backend exception, remembering it for the rest of the process."""
    global _unavailable
    error = _classify(exc)
    _unavailable = error
    return error


def _guard() -> None:
    """Fail fast when this process already knows the keyring is unusable."""
    if keyring_is_disabled():
        raise KeyringUnavailableError(
            f"Secure local credential storage is disabled by {OPENSRE_DISABLE_KEYRING_ENV}.",
            reason=KeyringUnavailableReason.DISABLED,
        )
    if _unavailable is not None:
        raise _unavailable


def get(env_var: str) -> str:
    """Return the stored secret, or ``""`` when the keychain has no such entry.

    Raises :class:`KeyringUnavailableError` when the backend could not answer —
    a genuine miss and an unreachable keychain are different facts, and callers
    that collapse them persist bad state (a verified credential marked stale).
    """
    _guard()
    try:
        return (keyring.get_password(KEYRING_SERVICE, env_var) or "").strip()
    except (keyring.errors.KeyringError, RuntimeError, OSError) as exc:
        raise _unavailable_error(exc) from exc


def set(env_var: str, value: str) -> None:  # noqa: A001 - mirrors get/delete in this tier
    """Store a secret in the OS keychain."""
    _guard()
    try:
        keyring.set_password(KEYRING_SERVICE, env_var, value)
    except (keyring.errors.KeyringError, RuntimeError, OSError) as exc:
        raise _unavailable_error(exc) from exc


def delete(env_var: str) -> None:
    """Remove a secret from the OS keychain, tolerating an absent entry.

    Deliberately does *not* set the sticky flag: ``store.delete_secret`` already
    treats a failed keyring delete as non-fatal, so letting it mark the whole
    process unavailable would push every later read and write in the run onto
    the plaintext fallback because one logout could not reach the backend.
    """
    _guard()
    try:
        keyring.delete_password(KEYRING_SERVICE, env_var)
    except keyring.errors.PasswordDeleteError:
        return
    except (keyring.errors.KeyringError, RuntimeError, OSError) as exc:
        raise _classify(exc) from exc


def item_exists(env_var: str) -> bool | None:
    """Whether a macOS Keychain item exists, without reading its secret.

    Returns ``None`` when the question cannot be answered cheaply (not macOS, a
    different backend, no ``security`` binary), which means the caller must fall
    through to a real read.
    """
    # ``sys.platform`` rather than ``platform.system()``: this repo has its own
    # top-level ``platform`` package, so importing the stdlib one here creates a
    # config -> platform edge that import-linter has to allowlist.
    if sys.platform != "darwin" or not _is_macos_backend():
        return None
    security_bin = shutil.which("security")
    if security_bin is None:
        return None
    try:
        result = subprocess.run(
            [security_bin, "find-generic-password", "-s", KEYRING_SERVICE, "-a", env_var],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_MACOS_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == _MACOS_ITEM_NOT_FOUND_RETURNCODE:
        return False
    return None


__all__ = [
    "backend_name",
    "delete",
    "get",
    "item_exists",
    "keyring_is_disabled",
    "keyring_writes_enabled",
    "reset_keyring_state",
    "set",
]
