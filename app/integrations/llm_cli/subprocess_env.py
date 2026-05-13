"""Filtered environment for spawning LLM CLI subprocesses.

Only keys needed for binary resolution, locale, proxies, TLS, and adapter-specific
prefixes are forwarded from ``os.environ``. Adapter implementations merge their
own overrides (for example explicit API keys for that CLI only).
"""

from __future__ import annotations

import os

_SAFE_SUBPROCESS_ENV_KEYS = frozenset(
    {
        "HOME",
        # macOS Keychain item lookup (where `claude login` stores OAuth on darwin)
        # requires USER. LOGNAME is the POSIX/Linux equivalent kept for parity.
        "USER",
        "LOGNAME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "SHELL",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "TERM",
        "TZ",
        "NO_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NO_COLOR",
        "FORCE_COLOR",
        "COLORTERM",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    }
)
_SAFE_SUBPROCESS_ENV_PREFIXES = (
    "LC_",
    "CODEX_",
    "CURSOR_",
    "CLAUDE_",
    "GEMINI_",
    "GOOGLE_",
    "OPENCODE_",
    "KIMI_",
    # NOTE: deliberately NO ``COPILOT_`` entry. ``COPILOT_GITHUB_TOKEN`` is a
    # GitHub PAT; if we forwarded it via this prefix allowlist it would leak
    # into every other CLI subprocess (Codex, Kimi, Claude Code, etc.). All
    # Copilot envs (config + credentials) flow through ``CLIInvocation.env``
    # built by ``CopilotAdapter.build`` so they only reach the Copilot
    # subprocess. See ``env_overrides.py`` for the COPILOT_*_ENV_KEYS tuples.
)


def build_cli_subprocess_env(overrides: dict[str, str] | None) -> dict[str, str]:
    """Return a subprocess ``env`` dict: safe inherited keys plus optional overrides."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _SAFE_SUBPROCESS_ENV_KEYS or any(
            key.startswith(prefix) for prefix in _SAFE_SUBPROCESS_ENV_PREFIXES
        ):
            env[key] = value
    if overrides:
        env.update(overrides)
    return env
