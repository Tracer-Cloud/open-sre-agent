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
"""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path

from config.constants.paths import opensre_home
from config.secrets import local_file, os_keyring
from config.secrets.backend import KeyringUnavailableError

logger = logging.getLogger(__name__)

_MARKER_FILENAME = "keychain-imported"


def _marker_path() -> Path:
    return opensre_home() / _MARKER_FILENAME


def _candidate_env_vars() -> tuple[str, ...]:
    """Secret names an earlier version could have written to the keychain.

    Includes LLM provider API keys and integration secret env constants that
    share the same keyring service (Telegram, Slack, Sentry, …).

    Reads API key names from :mod:`config.constants.llm` (a leaf) — never from
    ``config.llm_auth``, which would import ``config.secrets.store`` and cycle.
    """
    import config.constants as constants
    from config.constants.llm import OPEN_SRE_API_KEY_ENV_NAMES
    from config.env_key_sensitivity import is_sensitive_env_key

    names: list[str] = list(OPEN_SRE_API_KEY_ENV_NAMES)
    for attr in dir(constants):
        if attr != "POSTHOG_CAPTURE_API_KEY" and not attr.endswith("_ENV"):
            continue
        value = getattr(constants, attr, None)
        if isinstance(value, str) and value and is_sensitive_env_key(value):
            names.append(value)
    # Deduplicate, stable order so the approval sequence is reproducible.
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


def _scrub_keychain(env_var: str) -> None:
    """Best-effort remove of a migrated keychain copy. Never raises."""
    if os_keyring.keyring_is_disabled():
        return
    with suppress(KeyringUnavailableError, OSError, RuntimeError):
        os_keyring.delete(env_var)


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
    successfully — a locked keychain leaves the import pending for the next run.
    Migrated (and already-local) names are scrubbed from the keychain so logout
    cannot leave a recoverable OS copy behind.
    """
    if _already_imported() or os_keyring.keyring_is_disabled():
        return ()

    imported: list[str] = []
    complete = True
    for env_var in _candidate_env_vars():
        if local_file.get(env_var):
            # Local file wins; still drop a stale keychain duplicate.
            _scrub_keychain(env_var)
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
        _scrub_keychain(env_var)

    if complete:
        _mark_imported()
    return tuple(imported)


__all__ = ["import_keychain_secrets_once"]
