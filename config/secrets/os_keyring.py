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
from typing import Any

import keyring
import keyring.errors

from config.constants.secrets import (
    KEYRING_SERVICE,
    OPENSRE_DISABLE_KEYRING_ENV,
)
from config.secrets.backend import KeyringUnavailableError, KeyringUnavailableReason

_DISABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_MACOS_PROBE_TIMEOUT_SECONDS = 2.0
_MACOS_DUMP_TIMEOUT_SECONDS = 30.0
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

    Deliberately does *not* set the sticky unavailability flag: a one-shot
    delete failure must not poison later reads in the same process.

    Nor does it honour ``OPENSRE_DISABLE_KEYRING``. That switch declines to
    *store* secrets locally; refusing to delete on top of it left a revoked
    credential in the keychain, recoverable by unsetting the flag. Removing a
    secret is safe on a machine that has opted out of keeping any.

    Callers that need revocation (logout, migration scrub) must treat
    :class:`KeyringUnavailableError` as failure except ``NO_BACKEND``.
    """
    if _unavailable is not None:
        raise _unavailable
    try:
        keyring.delete_password(KEYRING_SERVICE, env_var)
    except keyring.errors.PasswordDeleteError:
        return
    except (keyring.errors.KeyringError, RuntimeError, OSError) as exc:
        raise _classify(exc) from exc


def _is_secret_service_backend() -> bool:
    try:
        backend = _backend()
    except Exception:
        return False
    return backend.__class__.__module__.startswith("keyring.backends.SecretService")


def supports_username_enumeration() -> bool:
    """Whether this process can list account names under ``KEYRING_SERVICE``.

    macOS Keychain + ``security dump-keychain`` (metadata only, no ``-d``) and
    Freedesktop Secret Service (search by service attribute) can surface every
    account for our service — including names the finite constants scan never
    knew about. Other backends have no portable list API in ``keyring``.
    """
    if sys.platform == "darwin" and _is_macos_backend() and shutil.which("security"):
        return True
    return _is_secret_service_backend()


def list_usernames() -> tuple[str, ...] | None:
    """Account names stored under ``KEYRING_SERVICE``, without reading secrets.

    Returns ``None`` when enumeration is unsupported or the listing failed — the
    one-time importer must not mark itself done on an enumerable platform when
    this is ``None``, or dynamically named leftovers stay recoverable forever.
    """
    if sys.platform == "darwin" and _is_macos_backend():
        return _list_macos_usernames()
    if _is_secret_service_backend():
        return _list_secret_service_usernames()
    return None


def _list_macos_usernames() -> tuple[str, ...] | None:
    security_bin = shutil.which("security")
    if security_bin is None:
        return None
    try:
        result = subprocess.run(
            [security_bin, "dump-keychain"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_MACOS_DUMP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _usernames_for_service(result.stdout, KEYRING_SERVICE)


def _list_secret_service_usernames() -> tuple[str, ...] | None:
    """List usernames for ``KEYRING_SERVICE`` via Secret Service attributes."""
    from contextlib import closing

    try:
        # The Secret Service backend publishes no typed surface for any of this
        # — ``schemes``/``scheme`` and ``_query`` are internals — so it is bound
        # as ``Any`` rather than annotated with a shape this package would then
        # have to keep in step with ``keyring``.
        backend: Any = _backend()
        scheme = backend.schemes[backend.scheme]
        username_key = scheme["username"]
        collection = backend.get_preferred_collection()
        with closing(collection.connection):
            items = collection.search_items(backend._query(KEYRING_SERVICE))
            names: list[str] = []
            for item in items:
                attrs = item.get_attributes()
                name = attrs.get(username_key) if isinstance(attrs, dict) else None
                if isinstance(name, str) and name:
                    names.append(name)
            return tuple(dict.fromkeys(names))
    except Exception:
        return None


def _usernames_for_service(dump_text: str, service: str) -> tuple[str, ...]:
    """Parse ``security dump-keychain`` metadata for accounts under ``service``."""
    names: list[str] = []
    current_acct: str | None = None
    current_svce: str | None = None

    def _flush() -> None:
        nonlocal current_acct, current_svce
        if current_svce == service and current_acct:
            names.append(current_acct)
        current_acct = None
        current_svce = None

    for raw_line in dump_text.splitlines():
        line = raw_line.strip()
        if line.startswith("keychain:") or line.startswith("class:"):
            _flush()
            continue
        if not line.startswith('"'):
            continue
        if line.startswith('"acct"'):
            current_acct = _blob_string(line)
        elif line.startswith('"svce"'):
            current_svce = _blob_string(line)
    _flush()
    return tuple(dict.fromkeys(name for name in names if name))


def _blob_string(attribute_line: str) -> str | None:
    """Extract the quoted string from a dump-keychain attribute line."""
    marker = '<blob>="'
    start = attribute_line.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = attribute_line.find('"', start)
    if end < 0:
        return None
    return attribute_line[start:end] or None


def item_exists(env_var: str) -> bool | None:
    """Whether a macOS Keychain item exists, without reading its secret.

    Returns ``None`` when the question cannot be answered cheaply (not macOS, a
    different backend, no ``security`` binary), which means the caller must fall
    through to a real read.
    """
    # ``sys.platform`` avoids importing the stdlib ``platform`` module for a
    # single OS lookup.
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
    "list_usernames",
    "reset_keyring_state",
    "set",
    "supports_username_enumeration",
]
