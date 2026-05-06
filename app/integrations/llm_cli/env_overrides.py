"""Pick CLI subprocess ``env`` overrides from ``os.environ``.

``build_cli_subprocess_env`` only forwards a safe key/prefix subset from the parent process.
Vendor CLIs still need HTTP credentials sometimes; adapters merge ``nonempty_env_values(...)``
into ``CLIInvocation.env`` (same idea as Codex ``OPENAI_*``, Cursor ``CURSOR_API_KEY``, OpenCode HTTP keys).

Keep ``HTTP_LLM_PROVIDER_ENV_KEYS`` aligned with ``LLMSettings`` / ``app/config.py`` API-key env
names when adding HTTP LLM providers.
"""

from __future__ import annotations

import os
from typing import Final

OPENAI_PLATFORM_ENV_KEYS: Final[tuple[str, ...]] = (
    "OPENAI_API_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_BASE_URL",
)

HTTP_LLM_PROVIDER_ENV_KEYS: Final[tuple[str, ...]] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "REQUESTY_API_KEY",
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
    "MINIMAX_API_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_BASE_URL",
)

ANTHROPIC_CLI_ENV_KEYS: Final[tuple[str, ...]] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
)

CURSOR_CLI_ENV_KEYS: Final[tuple[str, ...]] = ("CURSOR_API_KEY",)


def nonempty_env_values(keys: tuple[str, ...]) -> dict[str, str]:
    """Return ``{name: value}`` for keys with non-empty stripped values in ``os.environ``."""
    out: dict[str, str] = {}
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            out[key] = val
    return out
