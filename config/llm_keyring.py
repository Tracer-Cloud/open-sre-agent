"""Low-level OS keyring storage for OpenSRE secrets.

Historically named for LLM API keys; the same store backs integration secrets
(``TELEGRAM_BOT_TOKEN``, ``ROCKETCHAT_AUTH_TOKEN``, etc.) via
``save_keyring_secret`` / ``resolve_env_credential``. The keyring service id
remains ``opensre.llm`` so existing entries keep resolving.

Two behaviors live here that are easy to get wrong with a bare
``keyring.get_password``/``set_password`` per call:

* **Repeat OS prompts** — macOS/GNOME Keychain backends can re-prompt for
  authorization on every distinct call. A single onboarding run may read or
  write the same ``env_var`` more than once (status check, save, verify), so
  ``_KeyringSession`` caches per-process results and invalidates them on
  save/delete, capping each credential at one backend round trip per process.
* **No fallback when the backend is unreachable** — a locked macOS Keychain
  or a headless Linux box with no D-Bus session makes every keyring call fail
  identically. Once that happens once, ``_KeyringSession.backend_unavailable``
  short-circuits further attempts for the rest of the process (no retry
  storm), and :func:`save_secret_with_fallback` persists to a local JSON
  store (see :func:`fallback_store_path`) instead of failing onboarding
  outright.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Final

import keyring
import keyring.errors

import platform

from config.constants.paths import OPENSRE_HOME_DIR

_KEYRING_SERVICE: Final = "opensre.llm"
RECORD_PREFIX: Final = "record:"
_DISABLED_VALUES: Final = frozenset({"1", "true", "yes", "on"})
OPENSRE_FALLBACK_SECRETS_PATH_ENV: Final = "OPENSRE_FALLBACK_SECRETS_PATH"


def fallback_store_path() -> Path:
    """Return the local fallback secrets file, honoring the test/override env var.

    Mirrors ``config.local_env.get_project_env_path``'s override pattern so
    tests can redirect this off the developer's real ``~/.opensre`` (see
    ``tests/conftest.py::_isolate_opensre_home_files`` and its #3721 note).
    """
    override = os.getenv(OPENSRE_FALLBACK_SECRETS_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return OPENSRE_HOME_DIR / "secrets.local.json"


class _KeyringSession:
    """Per-process memo of keyring reads and backend health.

    Reset happens naturally at process exit — onboarding, ``opensre``
    subcommands, and tests each get a fresh session.
    """

    reads: dict[str, str] = {}
    backend_unavailable: bool = False


def _remember_read(env_var: str, value: str) -> None:
    _KeyringSession.reads[env_var] = value


def _forget_read(env_var: str) -> None:
    _KeyringSession.reads.pop(env_var, None)


def reset_keyring_session() -> None:
    """Clear the per-process cache; test-only, real processes never need this."""
    _KeyringSession.reads.clear()
    _KeyringSession.backend_unavailable = False


def keyring_is_disabled() -> bool:
    return os.getenv("OPENSRE_DISABLE_KEYRING", "").strip().lower() in _DISABLED_VALUES


def _is_macos_keyring_backend() -> bool:
    backend = keyring.get_keyring()
    return backend.__class__.__module__.startswith("keyring.backends.macOS")


def macos_keychain_item_exists(username: str) -> bool | None:
    """Return whether a macOS Keychain item exists without reading its secret."""
    if platform.system() != "Darwin":
        return None
    if not _is_macos_keyring_backend():
        return None
    security_bin = shutil.which("security")
    if security_bin is None:
        return None
    try:
        result = subprocess.run(
            [
                security_bin,
                "find-generic-password",
                "-s",
                _KEYRING_SERVICE,
                "-a",
                username,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 44:
        return False
    return None


def read_keychain_secret(env_var: str) -> str:
    """Read a secret directly from the system keychain, backend errors uncaught.

    Callers that must tell "genuinely absent" apart from "keychain backend
    could not be reached right now" (e.g. deciding whether to persist a
    credential as stale) should use this instead of ``resolve_keyring_secret``,
    which collapses both cases to ``""``.

    Cached for the rest of the process once read successfully, so a status
    check followed by a save/verify in the same run only ever hits the OS
    backend once per ``env_var`` (see #3295: macOS re-prompts per call).
    """
    if env_var in _KeyringSession.reads:
        return _KeyringSession.reads[env_var]
    value = (keyring.get_password(_KEYRING_SERVICE, env_var) or "").strip()
    _remember_read(env_var, value)
    return value


def resolve_keyring_secret(env_var: str) -> str:
    """Read a secret from the OS keyring only (empty if missing/disabled/error).

    Prefer :func:`config.llm_credentials.resolve_env_credential` when callers
    should also honor a process-env value.

    Backend init failures (e.g. SecretService raising bare ``RuntimeError`` when
    D-Bus is unset) are treated as miss — catalog/env loaders must not abort.
    A failure also flips :attr:`_KeyringSession.backend_unavailable` so later
    calls in the same process skip straight to the fallback store instead of
    repeating a doomed backend call.
    """
    if keyring_is_disabled() or _KeyringSession.backend_unavailable:
        return ""
    try:
        return read_keychain_secret(env_var)
    except (keyring.errors.KeyringError, RuntimeError, OSError):
        _KeyringSession.backend_unavailable = True
        return ""


def _keyring_backend_name() -> str:
    backend = keyring.get_keyring()
    return f"{backend.__class__.__module__}.{backend.__class__.__name__}"


def get_keyring_setup_instructions(env_var: str) -> tuple[str, ...]:
    """Return platform-specific guidance for fixing secure credential storage."""
    if keyring_is_disabled():
        return (
            "Secure local credential storage is disabled by OPENSRE_DISABLE_KEYRING.",
            f"Unset OPENSRE_DISABLE_KEYRING and rerun `opensre onboard` to save {env_var} securely.",
        )

    backend_name = _keyring_backend_name()
    if platform.system() == "Linux":
        lines = [f"Current keyring backend: {backend_name}."]
        if shutil.which("gnome-keyring-daemon") is None:
            lines.append("This Ubuntu or EC2 instance is missing the GNOME Keyring daemon.")
            lines.append(
                "Install it first: sudo apt update && sudo apt install -y gnome-keyring dbus-user-session"
            )
        elif not os.getenv("DBUS_SESSION_BUS_ADDRESS", "").strip():
            lines.append(
                "GNOME Keyring is installed, but this shell is not running inside a D-Bus session."
            )
        else:
            lines.append(
                "This shell has D-Bus available, but the login keyring is still locked or not initialized."
            )

        lines.extend(
            [
                "Start a D-Bus shell: dbus-run-session -- sh",
                "Inside that shell unlock the keyring: echo '<choose-a-keyring-password>' | gnome-keyring-daemon --unlock",
                "Then rerun `opensre onboard` in that same shell.",
                "For deeper diagnostics run `python -m keyring diagnose`.",
            ]
        )
        return tuple(lines)

    return (
        f"Current keyring backend: {backend_name}.",
        "Make sure your system keychain service is installed and unlocked, then rerun `opensre onboard`.",
        "For deeper diagnostics run `python -m keyring diagnose`.",
    )


def save_keyring_secret(env_var: str, value: str) -> None:
    """Persist a secret in the user's system keychain under ``env_var``."""
    normalized = value.strip()
    if not normalized:
        delete_keyring_secret(env_var)
        return
    if keyring_is_disabled() or _KeyringSession.backend_unavailable:
        raise RuntimeError("Secure local credential storage is unavailable on this machine.")
    try:
        keyring.set_password(_KEYRING_SERVICE, env_var, normalized)
    except keyring.errors.KeyringError as exc:
        _KeyringSession.backend_unavailable = True
        raise RuntimeError(
            "Secure local credential storage is unavailable on this machine."
        ) from exc
    _remember_read(env_var, normalized)


def delete_keyring_secret(env_var: str) -> None:
    """Remove a secret from the user's system keychain if present."""
    _forget_read(env_var)
    if keyring_is_disabled():
        return
    try:
        keyring.delete_password(_KEYRING_SERVICE, env_var)
    except keyring.errors.KeyringError:
        return


def _read_fallback_store() -> dict[str, str]:
    path = fallback_store_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_fallback_store(payload: Mapping[str, str]) -> None:
    path = fallback_store_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with suppress(OSError):
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def save_fallback_secret(env_var: str, value: str) -> None:
    """Persist a secret to the local fallback store (used when keyring is unavailable).

    Owner-only-permissioned JSON under ``OPENSRE_HOME_DIR``, distinct from the
    project ``.env`` that :mod:`config.env_file` deliberately keeps secret-free.
    """
    data = _read_fallback_store()
    normalized = value.strip()
    if normalized:
        data[env_var] = normalized
    else:
        data.pop(env_var, None)
    _write_fallback_store(data)


def resolve_fallback_secret(env_var: str) -> str:
    """Read a secret from the local fallback store only (empty if absent)."""
    return _read_fallback_store().get(env_var, "")


def delete_fallback_secret(env_var: str) -> None:
    """Remove a secret from the local fallback store if present."""
    data = _read_fallback_store()
    if env_var in data:
        del data[env_var]
        _write_fallback_store(data)


def save_secret_with_fallback(env_var: str, value: str) -> str:
    """Save to the OS keyring, falling back to local storage if it's unavailable.

    Returns ``"keyring"`` or ``"fallback"`` for the tier actually used, so
    callers can tell the user their credential is not protected by the OS
    keychain. Raises only when neither tier can persist the value.
    """
    if not value.strip():
        delete_keyring_secret(env_var)
        delete_fallback_secret(env_var)
        return "keyring"
    try:
        save_keyring_secret(env_var, value)
        delete_fallback_secret(env_var)
        return "keyring"
    except RuntimeError:
        try:
            save_fallback_secret(env_var, value)
        except OSError as fallback_exc:
            raise RuntimeError(
                "Secure local credential storage is unavailable on this machine, "
                f"and the local fallback store could not be written either: {fallback_exc}."
            ) from fallback_exc
        return "fallback"


def resolve_secret_with_fallback(env_var: str) -> str:
    """Read a secret from the keyring, then the local fallback store."""
    return resolve_keyring_secret(env_var) or resolve_fallback_secret(env_var)


def _record_username(record_name: str) -> str:
    normalized = record_name.strip()
    if not normalized:
        raise ValueError("record_name must not be empty")
    return f"{RECORD_PREFIX}{normalized}"


def save_llm_credential_record(record_name: str, values: Mapping[str, str]) -> None:
    """Persist a small JSON credential metadata record in the system keychain."""
    normalized = {
        str(key).strip(): str(value).strip()
        for key, value in values.items()
        if str(key).strip() and str(value).strip()
    }
    if not normalized:
        delete_llm_credential_record(record_name)
        return
    if keyring_is_disabled():
        raise RuntimeError("Secure local credential storage is disabled on this machine.")
    try:
        keyring.set_password(
            _KEYRING_SERVICE,
            _record_username(record_name),
            json.dumps(normalized, sort_keys=True),
        )
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(
            "Secure local credential storage is unavailable on this machine."
        ) from exc


def resolve_llm_credential_record(record_name: str) -> dict[str, str]:
    """Resolve a JSON credential metadata record from the local keychain."""
    if keyring_is_disabled():
        return {}
    try:
        raw = keyring.get_password(_KEYRING_SERVICE, _record_username(record_name)) or ""
    except keyring.errors.KeyringError:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in parsed.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def delete_llm_credential_record(record_name: str) -> None:
    """Remove a JSON credential metadata record from the local keychain."""
    if keyring_is_disabled():
        return
    try:
        keyring.delete_password(_KEYRING_SERVICE, _record_username(record_name))
    except (keyring.errors.KeyringError, ValueError):
        return
