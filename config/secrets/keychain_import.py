"""One-time move of OS-keychain secrets into the local credential file.

Reads no longer consult the keychain for resolution, so secrets written by
earlier versions would otherwise become invisible. This runs until it can
finish a full probe of every candidate name, copies what it finds, deletes the
keychain copies it migrated, and only then writes a marker — and only when the
platform can enumerate keychain usernames so dynamically named leftovers are
not stranded.

Only entries that :func:`config.secrets.os_keyring.item_exists` reports as
present are read when that probe is definitive. When the probe is indeterminate
(``None``), a real read is attempted so Linux/Windows and macOS without
``security`` still migrate. A locked or unreachable keychain leaves the marker
unwritten so a later run can finish the move.

On platforms that can enumerate (macOS dump, Secret Service search), account
names are discovered so ``record:…`` and custom integration env vars are not
stranded after the finite constants scan finishes. Elsewhere, known names are
still migrated, the permanent marker is never written, and
:func:`import_named_keychain_secret` covers a looked-up dynamic name on demand.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.constants.paths import opensre_home
from config.secrets import local_file, os_keyring
from config.secrets.backend import KeyringUnavailableError

logger = logging.getLogger(__name__)

# Log messages here are fixed strings. The env var *name* is enough for CodeQL's
# clear-text-logging rule to flag the sink (``*_SECRET``/``*_PASSWORD`` names
# taint), and the traceback already identifies which step failed.

_MARKER_FILENAME = "keychain-imported"

# Per-process skip after a clean known-name pass on a non-enumerable platform.
# Stored in a dict so this module both reads and writes the flag (a bare
# ``global`` bool is often reported unused by CodeQL). The disk marker is not
# written on those platforms — :func:`import_named_keychain_secret` still
# migrates dynamic names on demand.
_IMPORT_STATE = {"pass_attempted": False}

# Names copied locally whose keychain delete failed. Once local, lookup never
# revisits migration, so these must be retried.
_pending_scrubs: set[str] = set()


def reset_import_state() -> None:
    """Forget the per-process pass flag and pending scrubs (test hook)."""
    _IMPORT_STATE["pass_attempted"] = False
    _pending_scrubs.clear()


def retry_pending_scrubs() -> None:
    """Re-attempt keychain deletes that failed after the value was copied."""
    for env_var in tuple(_pending_scrubs):
        if _scrub_keychain(env_var):
            _pending_scrubs.discard(env_var)


def _pass_was_attempted() -> bool:
    return bool(_IMPORT_STATE["pass_attempted"])


def _mark_pass_attempted() -> None:
    _IMPORT_STATE["pass_attempted"] = True


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


def _local_credential_names() -> tuple[str, ...] | None:
    """Names in the local file, or ``None`` when the store could not be listed."""
    try:
        return local_file.keys()
    except local_file.LOCAL_STORE_ERRORS:
        logger.debug("Could not list local credential names", exc_info=True)
        return None


def _candidate_env_vars(
    *,
    discovered: tuple[str, ...] = (),
    local_names: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Union of known constants, local-file keys, and keychain-enumerated names."""
    names: list[str] = list(_constant_candidate_env_vars())
    names.extend(local_names)
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
        logger.debug("Keychain scrub refused by the backend", exc_info=True)
        return False
    except (OSError, RuntimeError):
        logger.debug("Keychain scrub raised an unexpected error", exc_info=True)
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
        logger.debug("Keychain existence check failed", exc_info=True)
        return None, False
    if exists is False:
        return None, True
    # True (present) or None (indeterminate): attempt a real read so backends
    # without a cheap existence probe still migrate.
    try:
        return os_keyring.get(env_var), True
    except (KeyringUnavailableError, OSError, RuntimeError):
        logger.debug("Keychain read failed", exc_info=True)
        return None, False


def _migrate_one(env_var: str) -> tuple[str, bool]:
    """Migrate or scrub one name. Returns ``(imported_value, ok)``.

    ``ok`` is False when a lock/keychain failure means the pass must stay pending.
    ``imported_value`` is non-empty only when a keychain secret was copied in.
    """
    try:
        already_local = bool(local_file.get(env_var))
    except local_file.LOCAL_STORE_ERRORS:
        logger.debug("Could not read the local credential store", exc_info=True)
        return "", False
    if already_local:
        return "", _scrub_keychain(env_var)
    value, ok = _read_keychain_value(env_var)
    if not ok:
        return "", False
    if not value:
        return "", True
    try:
        local_file.set(env_var, value)
    except local_file.LOCAL_STORE_ERRORS:
        logger.debug("Could not persist an imported credential", exc_info=True)
        return "", False
    if not _scrub_keychain(env_var):
        _pending_scrubs.add(env_var)
        return value, False
    _pending_scrubs.discard(env_var)
    return value, True


def import_named_keychain_secret(env_var: str) -> str:
    """Migrate one keychain secret by exact name. Never raises.

    Used when the platform cannot enumerate usernames: a dynamic name absent
    from the constants catalog becomes visible the first time something looks
    it up, instead of staying stranded in the retired keychain tier.

    Once the permanent marker exists, the keychain is fully retired for reads —
    including on-demand — so absent names do not reopen it.
    """
    if not env_var or os_keyring.keyring_is_disabled() or _already_imported():
        return ""
    value, _ok = _migrate_one(env_var)
    return value


def import_keychain_secrets_once() -> tuple[str, ...]:
    """Copy keychain secrets into the local file. Returns the names imported.

    Never raises. The permanent marker is written only after every candidate was
    probed, every keychain copy that needed scrubbing was deleted, **and** the
    platform enumerated keychain usernames — otherwise a dynamically named
    leftover would never be discovered after the marker lands.

    On non-enumerable platforms a clean known-name pass still sets a per-process
    skip so lookups do not re-scan the catalog every time; named leftovers use
    :func:`import_named_keychain_secret`.
    """
    if os_keyring.keyring_is_disabled():
        return ()
    retry_pending_scrubs()
    if _already_imported() or _pass_was_attempted():
        return ()

    imported: list[str] = []
    try:
        discovered: tuple[str, ...] = ()
        can_finalize = False
        complete = True
        if os_keyring.supports_username_enumeration():
            listed = os_keyring.list_usernames()
            if listed is None:
                complete = False
            else:
                discovered = listed
                can_finalize = True
        # else: cannot certify the keychain has no unknown names — never finalize.

        local_names = _local_credential_names()
        if local_names is None:
            # Dropping dynamic local names would skip scrubbing their keychain twins.
            complete = False
            local_names = ()

        for env_var in _candidate_env_vars(discovered=discovered, local_names=local_names):
            value, ok = _migrate_one(env_var)
            if value:
                imported.append(env_var)
            if not ok:
                complete = False

        if complete and can_finalize:
            _mark_imported()
        if complete:
            # Known-name pass finished cleanly. Non-enumerable platforms stay
            # without a disk marker so a later release can still grow discovery;
            # this process does not repeat the catalog scan.
            _mark_pass_attempted()
    except local_file.LOCAL_STORE_ERRORS:
        # Belt-and-suspenders: no credential-store failure may escape into lookup.
        # Leave the pass unfinished (no marker / no pass_attempted).
        logger.debug("Keychain import interrupted by the local credential store", exc_info=True)
        return tuple(imported)
    return tuple(imported)


__all__ = [
    "import_keychain_secrets_once",
    "import_named_keychain_secret",
    "reset_import_state",
]
