"""API-key persistence and its error type.

Leaf module — imports nothing from ``surfaces.cli.wizard``, so ``wizard._ui`` can
depend on it without re-forming the
``_ui → service → validation → azure_openai → _ui`` import cycle.
"""

from __future__ import annotations

from collections.abc import Callable

from config.llm_credentials import save_llm_api_key


class AuthSetupError(RuntimeError):
    """Raised when provider auth setup cannot complete."""


SaveSecret = Callable[[str, str], None]


def persist_api_key_secret(
    env_var: str,
    value: str,
    *,
    save_secret: SaveSecret = save_llm_api_key,
) -> None:
    """Persist one API-key secret through the shared auth service boundary."""
    try:
        save_secret(env_var, value)
    except RuntimeError as exc:
        raise AuthSetupError(str(exc)) from exc
