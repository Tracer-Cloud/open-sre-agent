"""Secure local storage helpers for LLM credentials."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from typing import Final

import keyring
import keyring.errors

import platform

_KEYRING_SERVICE: Final = "opensre.llm"
_RECORD_PREFIX: Final = "record:"
_DISABLED_VALUES: Final = frozenset({"1", "true", "yes", "on"})


def _keyring_is_disabled() -> bool:
    return os.getenv("OPENSRE_DISABLE_KEYRING", "").strip().lower() in _DISABLED_VALUES


def _is_macos_keyring_backend() -> bool:
    backend = keyring.get_keyring()
    return backend.__class__.__module__.startswith("keyring.backends.macOS")


def _macos_keychain_item_exists(username: str) -> bool | None:
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


def resolve_env_credential(env_var: str, *, default: str = "") -> str:
    """Resolve a credential from env first, then the local keychain."""
    env_value = os.getenv(env_var, default).strip()
    if env_value:
        return env_value
    return resolve_llm_api_key(env_var)


def resolve_llm_api_key(env_var: str) -> str:
    """Resolve an LLM API key from env first, then the local keychain."""
    env_value = os.getenv(env_var, "").strip()
    if env_value:
        return env_value
    if _keyring_is_disabled():
        return ""
    try:
        return (keyring.get_password(_KEYRING_SERVICE, env_var) or "").strip()
    except keyring.errors.KeyringError:
        return ""


def llm_api_key_source(env_var: str) -> str:
    """Return where an LLM credential resolves from: ``env``, ``keyring``, or ``none``."""
    from config.llm_auth.credentials import source_for_api_key_env
    from config.llm_auth.provider_catalog import API_KEY_PROVIDER_ENVS

    prompt_safe_source = source_for_api_key_env(env_var)
    if prompt_safe_source != "none":
        return prompt_safe_source
    if env_var.strip() in set(API_KEY_PROVIDER_ENVS.values()):
        return "none"
    if os.getenv(env_var, "").strip():
        return "env"
    if _keyring_is_disabled():
        return "none"
    item_exists = _macos_keychain_item_exists(env_var)
    if item_exists is not None:
        return "keyring" if item_exists else "none"
    try:
        if (keyring.get_password(_KEYRING_SERVICE, env_var) or "").strip():
            return "keyring"
    except keyring.errors.KeyringError:
        return "none"
    return "none"


def has_llm_api_key(env_var: str) -> bool:
    """Return True when an API key is available from env or secure local storage."""
    from config.llm_auth.credentials import has_api_key_env_status
    from config.llm_auth.provider_catalog import API_KEY_PROVIDER_ENVS

    if has_api_key_env_status(env_var):
        return True
    if env_var.strip() in set(API_KEY_PROVIDER_ENVS.values()):
        return False
    if os.getenv(env_var, "").strip():
        return True
    if _keyring_is_disabled():
        return False
    item_exists = _macos_keychain_item_exists(env_var)
    if item_exists is not None:
        return item_exists
    return bool(resolve_llm_api_key(env_var))


def _keyring_backend_name() -> str:
    backend = keyring.get_keyring()
    return f"{backend.__class__.__module__}.{backend.__class__.__name__}"


def get_keyring_setup_instructions(env_var: str) -> tuple[str, ...]:
    """Return platform-specific guidance for fixing secure credential storage."""
    if _keyring_is_disabled():
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


def save_llm_api_key(env_var: str, value: str) -> None:
    """Persist an LLM API key in the user's system keychain."""
    normalized = value.strip()
    if not normalized:
        delete_llm_api_key(env_var)
        return
    if _keyring_is_disabled():
        raise RuntimeError("Secure local credential storage is disabled on this machine.")
    try:
        keyring.set_password(_KEYRING_SERVICE, env_var, normalized)
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(
            "Secure local credential storage is unavailable on this machine."
        ) from exc


def delete_llm_api_key(env_var: str) -> None:
    """Remove an LLM API key from the user's system keychain if present."""
    if _keyring_is_disabled():
        return
    try:
        keyring.delete_password(_KEYRING_SERVICE, env_var)
    except keyring.errors.KeyringError:
        return


def _record_username(record_name: str) -> str:
    normalized = record_name.strip()
    if not normalized:
        raise ValueError("record_name must not be empty")
    return f"{_RECORD_PREFIX}{normalized}"


def save_llm_credential_record(record_name: str, values: Mapping[str, str]) -> None:
    """Persist a small JSON credential metadata record in the system keychain.

    These records are for auth metadata such as provider/source/status. API keys
    still use :func:`save_llm_api_key`; OAuth or vendor CLI tokens must remain
    in the vendor-owned credential store unless the vendor returns a supported
    API credential explicitly meant for OpenSRE.
    """
    normalized = {
        str(key).strip(): str(value).strip()
        for key, value in values.items()
        if str(key).strip() and str(value).strip()
    }
    if not normalized:
        delete_llm_credential_record(record_name)
        return
    if _keyring_is_disabled():
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
    if _keyring_is_disabled():
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
    if _keyring_is_disabled():
        return
    try:
        keyring.delete_password(_KEYRING_SERVICE, _record_username(record_name))
    except (keyring.errors.KeyringError, ValueError):
        return
