"""User-facing guidance for when secret storage did not work as intended.

Split from the storage tiers themselves so ``os_keyring`` stays free of copy and
the wizard has one import for everything it needs to explain.
"""

from __future__ import annotations

import os
import shutil
import sys

from config.constants.secrets import (
    OPENSRE_CREDENTIAL_FALLBACK_PATH_ENV,
    OPENSRE_DISABLE_CREDENTIAL_FALLBACK_ENV,
    OPENSRE_DISABLE_KEYRING_ENV,
)
from config.secrets import local_file, os_keyring, store


def get_keyring_setup_instructions(env_var: str) -> tuple[str, ...]:
    """Platform-specific guidance for restoring secure credential storage.

    Only reached once both tiers are out of the picture, so the goal is to name
    the switch that is refusing storage or the keychain that needs unlocking —
    not to walk a user through D-Bus when a working fallback would have saved
    the credential anyway.
    """
    if os_keyring.keyring_is_disabled():
        return (
            f"Secure local credential storage is disabled by {OPENSRE_DISABLE_KEYRING_ENV}.",
            f"Unset {OPENSRE_DISABLE_KEYRING_ENV} and rerun `opensre onboard` to save "
            f"{env_var}, or export {env_var} in your shell.",
        )

    if store.fallback_is_disabled():
        return (
            f"The local fallback store is disabled by {OPENSRE_DISABLE_CREDENTIAL_FALLBACK_ENV}.",
            f"Current keyring backend: {os_keyring.backend_name()}.",
            f"Unlock your system keychain, or export {env_var} in your shell.",
            f"To allow an owner-only file at {local_file.store_path()} instead, "
            f"unset {OPENSRE_DISABLE_CREDENTIAL_FALLBACK_ENV}.",
        )

    lines = [
        f"Current keyring backend: {os_keyring.backend_name()}.",
        f"OpenSRE could not use the system keychain or write {local_file.store_path()}.",
        f"Check that you can write to that path, or set "
        f"{OPENSRE_CREDENTIAL_FALLBACK_PATH_ENV} to a writable location.",
    ]
    if sys.platform.startswith("linux"):
        if shutil.which("gnome-keyring-daemon") is None:
            lines.append(
                "To use a real keychain instead: sudo apt update && "
                "sudo apt install -y gnome-keyring dbus-user-session"
            )
        elif not os.getenv("DBUS_SESSION_BUS_ADDRESS", "").strip():
            lines.append(
                "GNOME Keyring is installed but this shell has no D-Bus session; "
                "start one with: dbus-run-session -- sh"
            )
    lines.append(f"Or export {env_var} in your shell to skip local storage entirely.")
    lines.append("For deeper diagnostics run `python -m keyring diagnose`.")
    return tuple(lines)


__all__ = ["get_keyring_setup_instructions"]
