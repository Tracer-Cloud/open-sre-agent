"""One-time move of OS-keychain secrets into the local credential file.

Reads no longer consult the keychain for resolution, so secrets written by
earlier versions would otherwise become invisible. This runs until it can
finish a full probe of every candidate name, copies what it finds, deletes the
keychain copies it migrated, and only then writes a marker.

Only entries that :func:`config.secrets.os_keyring.item_exists` reports as
present are read when that probe is definitive. When the probe is indeterminate
(``None``), a real read is attempted so Linux/Windows and macOS without
``security`` still migrate. A locked or unreachable keychain leaves the marker
unwritten so a later run can finish the move.

On macOS, account names are also taken from a metadata-only keychain dump so
dynamically named secrets (``record:…``, custom integration env vars) are not
stranded after the finite constants scan finishes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.constants.paths import opensre_home
from config.secrets import local_file, os_keyring
from config.secrets.backend import KeyringUnavailableError

logger = logging.getLogger(__name__)

_MARKER_FILENAME = "keychain-imported"


def _marker_path() -> Path:
    return opensre_home() / _MARKER_FILENAME


def _constant_candidate_env_vars() -> tuple[str, ...]:
    """Secret names an earlier version could have written to the keychain.

    Includes LLM provider API keys and every sensitive ``*_ENV`` string under
    :mod:`config.constants` submodules (Telegram, Slack, Discord, Airflow, …).

    Scans each ``config.constants.*`` module rather than only the package
    ``__init__`` barrel — several integration constant modules are not
    re-exported there, and relying on ``dir(config.constants)`` left those
    keychain copies unmigrated. Never imports ``config.llm_auth`` or
    ``integrations`` (both pull ``config.secrets.store`` and would cycle).
    """
    import importlib
    import pkgutil

    import config.constants as constants_pkg
    from config.constants.llm import OPEN_SRE_API_KEY_ENV_NAMES
    from config.env_key_sensitivity import is_sensitive_env_key

    names: list[str] = list(OPEN_SRE_API_KEY_ENV_NAMES)
    for module_info in pkgutil.iter_modules(constants_pkg.__path__):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"config.constants.{module_info.name}")
        for attr in dir(module):
            if attr != "POSTHOG_CAPTURE_API_KEY" and not attr.endswith("_ENV"):
                continue
            value = getattr(module, attr, None)
            if isinstance(value, str) and value and is_sensitive_env_key(value):
                names.append(value)
    # Deduplicate, stable order so the approval sequence is reproducible.
    return tuple(dict.fromkeys(names))


def _candidate_env_vars(*, discovered: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Union of known constants, local-file keys, and keychain-enumerated names."""
    names: list[str] = list(_constant_candidate_env_vars())
    try:
        names.extend(local_file.keys())
    except OSError:
        logger.debug("Could not list local credential names", exc_info=True)
    names.extend(discovered)
    return tuple(dict.fromkeys(names))


def _already_imported() -> bool:
    try:
        return _marker_path().exists()
    except OSError:
        return False


def _mark_imported() -> None:
    path = _marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    except OSError:
        # Losing the marker costs one repeated check, not correctness.
        logger.debug("Could not write the keychain-import marker at %s", path)


def _scrub_keychain(env_var: str) -> bool:
    """Delete a keychain copy. True when gone (or never present).

    False when the backend could not answer — the caller must leave the import
    marker unwritten so a later run retries the scrub. No-backend machines have
    nothing to scrub, so they count as success.
    """
    from config.secrets.backend import KeyringUnavailableReason

    try:
        os_keyring.delete(env_var)
    except KeyringUnavailableError as exc:
        if exc.reason == KeyringUnavailableReason.NO_BACKEND:
            return True
        logger.debug("Keychain scrub failed for %s", env_var, exc_info=True)
        return False
    except (OSError, RuntimeError):
        logger.debug("Keychain scrub failed for %s", env_var, exc_info=True)
        return False
    return True


def _read_keychain_value(env_var: str) -> tuple[str | None, bool]:
    """Return ``(value, ok)``.

    ``ok`` is False when the keychain could not be probed — the import must stay
    pending. ``value`` is None when the name is known-absent.
    """
    try:
        exists = os_keyring.item_exists(env_var)
    except (OSError, RuntimeError, KeyringUnavailableError):
        logger.debug("Keychain existence probe failed for %s", env_var, exc_info=True)
        return None, False
    if exists is False:
        return None, True
    # True (present) or None (indeterminate): attempt a real read so backends
    # without a cheap existence probe still migrate.
    try:
        return os_keyring.get(env_var), True
    except (KeyringUnavailableError, OSError, RuntimeError):
        logger.debug("Keychain read failed for %s", env_var, exc_info=True)
        return None, False


def import_keychain_secrets_once() -> tuple[str, ...]:
    """Copy keychain secrets into the local file. Returns the names imported.

    Never raises. The marker is written only after every candidate was probed
    **and** every keychain copy that needed scrubbing was deleted — a locked
    keychain leaves the import pending so a later run can finish the scrub.

    When the platform can enumerate keychain usernames (macOS), a failed dump
    also leaves the import pending: otherwise a dynamically named leftover
    would never be discovered after the marker lands.
    """
    if _already_imported() or os_keyring.keyring_is_disabled():
        return ()

    imported: list[str] = []
    complete = True
    discovered: tuple[str, ...] = ()
    if os_keyring.supports_username_enumeration():
        listed = os_keyring.list_usernames()
        if listed is None:
            complete = False
        else:
            discovered = listed

    for env_var in _candidate_env_vars(discovered=discovered):
        try:
            already_local = bool(local_file.get(env_var))
        except OSError:
            # Lock timeout / unreadable store — leave migration pending; never
            # abort lookup/startup through this path.
            logger.debug("Could not read local credential for %s", env_var, exc_info=True)
            complete = False
            continue
        if already_local:
            # Local file wins; still drop a stale keychain duplicate.
            if not _scrub_keychain(env_var):
                complete = False
            continue
        value, ok = _read_keychain_value(env_var)
        if not ok:
            complete = False
            continue
        if not value:
            continue
        try:
            local_file.set(env_var, value)
        except OSError:
            logger.debug("Could not persist imported %s", env_var, exc_info=True)
            complete = False
            continue
        imported.append(env_var)
        if not _scrub_keychain(env_var):
            complete = False

    if complete:
        _mark_imported()
    return tuple(imported)


__all__ = ["import_keychain_secrets_once"]
