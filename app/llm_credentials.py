"""Secure local storage helpers for LLM API keys."""

from __future__ import annotations

import os
import platform
import shutil
from typing import Final

import keyring  # type: ignore[import-not-found,import-untyped]
import keyring.errors  # type: ignore[import-not-found,import-untyped]

_KEYRING_SERVICE: Final = "opensre.llm"
_DISABLED_VALUES: Final = frozenset({"1", "true", "yes", "on"})


def _keyring_is_disabled() -> bool:
    return os.getenv("OPENSRE_DISABLE_KEYRING", "").strip().lower() in _DISABLED_VALUES


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


def has_llm_api_key(env_var: str) -> bool:
    """Return True when an API key is available from env or secure local storage."""
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
