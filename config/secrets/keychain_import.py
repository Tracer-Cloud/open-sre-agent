"""One-time move of OS-keychain secrets into the local credential file.

Reads no longer consult the keychain, so secrets written by earlier versions
would otherwise become invisible. This runs once, copies what it finds, and
writes a marker so the keychain is never opened again.

Only entries that :func:`config.secrets.os_keyring.item_exists` reports as
present are read. Checking whether an item exists does not read its value, so
macOS asks for approval only for secrets the user actually has — not once per
name on the candidate list.
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


def _candidate_env_vars() -> tuple[str, ...]:
    """Secret names an earlier version could have written to the keychain.

    LLM provider API keys only. Integration secrets share the keychain service
    but have no catalog-wide list of env names to check, and every integration
    can be reconnected from its own setup command.
    """
    from config.llm_auth.provider_catalog import API_KEY_PROVIDER_ENVS

    # Deduplicate, stable order so the approval sequence is reproducible.
    return tuple(dict.fromkeys(env for env in API_KEY_PROVIDER_ENVS.values() if env))


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


def import_keychain_secrets_once() -> tuple[str, ...]:
    """Copy keychain secrets into the local file. Returns the names imported.

    Never raises: a locked, missing or unreachable keychain leaves the user
    with whatever the local file and environment already hold.
    """
    if _already_imported():
        return ()

    imported: list[str] = []
    keychain_faulted = False
    for env_var in _candidate_env_vars():
        if local_file.get(env_var):
            # The local file is authoritative; the keychain copy may be stale.
            continue
        try:
            if os_keyring.item_exists(env_var) is not True:
                continue
            value = os_keyring.get(env_var)
        except (KeyringUnavailableError, OSError):
            logger.debug("Keychain import skipped %s", env_var, exc_info=True)
            keychain_faulted = True
            continue
        if not value:
            continue
        try:
            local_file.set(env_var, value)
        except OSError:
            logger.debug("Could not persist imported %s", env_var, exc_info=True)
            keychain_faulted = True
            continue
        imported.append(env_var)

    # A locked keychain must stay pending: marking it done on a failed read
    # would strand secrets that are still there, with no way back.
    if not keychain_faulted:
        _mark_imported()
    return tuple(imported)


__all__ = ["import_keychain_secrets_once"]
