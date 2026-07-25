"""API-key persistence and its error type.

Leaf module — imports nothing from ``surfaces.cli.wizard``, so ``wizard._ui`` can
depend on it without re-forming the
``_ui → service → validation → azure_openai → _ui`` import cycle.
"""

from __future__ import annotations

from collections.abc import Callable

from config.llm_credentials import save_secret_with_fallback


class AuthSetupError(RuntimeError):
    """Raised when provider auth setup cannot complete."""


SaveSecret = Callable[[str, str], str]


def persist_api_key_secret(
    env_var: str,
    value: str,
    *,
    save_secret: SaveSecret = save_secret_with_fallback,
) -> str:
    """Persist one API-key secret through the shared auth service boundary.

    Returns the storage tier used (``"keyring"`` or ``"fallback"``) so the
    wizard can tell the user when a key is not protected by the OS keychain
    (#1403, #3348) instead of the save failing onboarding outright.
    """
    try:
        return save_secret(env_var, value)
    except RuntimeError as exc:
        raise AuthSetupError(str(exc)) from exc
