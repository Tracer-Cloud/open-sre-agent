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

# Non-credential Copilot CLI config envs forwarded only via the Copilot
# adapter's ``CLIInvocation.env``. They are deliberately NOT in
# ``_SAFE_SUBPROCESS_ENV_PREFIXES``: scoping them to the Copilot subprocess
# avoids confusing other vendor CLIs with vars they do not consume.
# ``GH_HOST`` / ``COPILOT_GH_HOST`` are hostname routing for GitHub Enterprise /
# alternate GitHub endpoints (same semantics as ``gh auth status --hostname``);
# Copilot CLI must see them alongside the auth probe.
COPILOT_CLI_CONFIG_ENV_KEYS: Final[tuple[str, ...]] = (
    "COPILOT_HOME",
    "COPILOT_MODEL",
    "COPILOT_GH_HOST",
    "GH_HOST",
)

# Copilot CLI credential envs. ``COPILOT_GITHUB_TOKEN`` is a GitHub PAT and
# MUST NOT flow through the global ``_SAFE_SUBPROCESS_ENV_PREFIXES`` allowlist
# (a ``COPILOT_`` prefix entry would forward this PAT into every CLI
# subprocess — Codex, Kimi, Claude Code, etc. — which is a credential-leak
# regression). The Copilot adapter forwards these *exclusively* via
# ``CLIInvocation.env`` so they only reach the Copilot subprocess.
# ``GH_TOKEN`` / ``GITHUB_TOKEN`` are non-prefixed for the same reason.
COPILOT_CLI_ENV_KEYS: Final[tuple[str, ...]] = (
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)


def nonempty_env_values(keys: tuple[str, ...]) -> dict[str, str]:
    """Return ``{name: value}`` for keys with non-empty stripped values in ``os.environ``."""
    out: dict[str, str] = {}
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            out[key] = val
    return out
