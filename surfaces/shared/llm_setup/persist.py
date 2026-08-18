"""API-key persistence and its error type."""

from __future__ import annotations

from collections.abc import Callable

from config.llm_credentials import save_keyring_secret
from config.secrets.store import SecretSaveResult


class AuthSetupError(RuntimeError):
    """Raised when provider auth setup cannot complete."""


SaveSecret = Callable[[str, str], SecretSaveResult]


def persist_api_key_secret(
    env_var: str,
    value: str,
    *,
    save_secret: SaveSecret = save_keyring_secret,
) -> SecretSaveResult:
    """Persist one API-key secret through the shared auth service boundary.

    Returns which storage tier accepted the write, so callers can report a
    fallback to the user instead of implying it reached the OS keychain.
    """
    try:
        return save_secret(env_var, value)
    except RuntimeError as exc:
        raise AuthSetupError(str(exc)) from exc
