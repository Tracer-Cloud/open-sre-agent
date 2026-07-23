"""Write credentials and settings to their correct storage tier.

OpenSRE persists configuration in three places, and which one a value belongs in
is decided by its **env var name**, not by the caller:

* **system keyring** — anything :func:`is_sensitive_env_key` classifies as a
  secret (``*_TOKEN``, ``*_KEY``, ``*_PASSWORD``, connection strings, …)
* **project ``.env``** — everything else (URLs, ids, channels, model names)
* the integration store — owned by ``integrations.store``, not this module

The split is enforced rather than advised: :func:`sync_env_values` refuses a
sensitive key and :func:`sync_env_secret` refuses a non-sensitive one, so a
mis-classified credential fails loudly instead of landing in clear text on disk.
Any ``.env`` rewrite also strips pre-existing secret assignments, so a file that
predates the keyring does not keep leaking.

This lives in ``config/`` — the layer floor — because every setup surface needs
it: the onboarding wizard (``surfaces/``), ``opensre integrations setup``, and
the interactive-shell action tools all persist the same credentials and must
agree on where they go. ``config/local_env.py`` already owns *reading* the
project env file; this owns writing it.
"""

from __future__ import annotations

import os
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from config.llm_auth.credentials import delete as delete_provider_auth
from config.llm_auth.credentials import save_api_key
from config.llm_auth.provider_catalog import API_KEY_PROVIDER_ENVS
from config.llm_credentials import delete_keyring_secret, save_keyring_secret
from config.local_env import get_project_env_path

PROJECT_ENV_PATH = get_project_env_path()

_ENV_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
# Names whose terminal token would otherwise flag them sensitive, but whose
# value is meant to be public: Discord's public key verifies signatures rather
# than authenticating, and MongoDB Atlas's public key is a paired identifier
# next to a private key, not a secret on its own.
_NON_SECRET_ENV_KEYS: frozenset[str] = frozenset({"DISCORD_PUBLIC_KEY", "MONGODB_ATLAS_PUBLIC_KEY"})
# Underscore-separated terminal tokens that mark an env var as sensitive.
# Matching the terminal component (rather than a substring or a fixed suffix
# like ``_token``) catches both ``GITLAB_ACCESS_TOKEN`` and a bare ``TOKEN``
# while leaving ``OPENAI_TOKEN_LIMIT`` (terminal ``limit``) alone.
_SENSITIVE_TERMINAL_TOKENS: frozenset[str] = frozenset(
    {
        "token",
        "secret",
        "password",
        "passwd",
        "key",
        "apikey",
        "credential",
        "credentials",
    }
)
_SENSITIVE_SUBSTRINGS: tuple[str, ...] = ("connection_string",)


@dataclass(frozen=True)
class _PublicEnvLines:
    """Validated `.env` content that contains no sensitive assignments."""

    lines: tuple[str, ...]

    @classmethod
    def from_lines(cls, lines: list[str]) -> _PublicEnvLines:
        public_lines = strip_secret_env_lines(lines)
        _ensure_no_sensitive_env_lines(public_lines)
        return cls(tuple(public_lines))

    def write_to(self, target_path: Path) -> None:
        with target_path.open("w", encoding="utf-8", newline="") as env_file:
            env_file.writelines(self.lines)


def env_assignment_key(line: str) -> str | None:
    """Return the env key a ``.env`` line assigns, or ``None`` for non-assignments."""
    match = _ENV_ASSIGNMENT.match(line)
    return match.group(1) if match else None


def is_sensitive_env_key(key: str) -> bool:
    """True when an env var should be stored in the keyring, not plain .env."""
    if key in _NON_SECRET_ENV_KEYS:
        return False
    lowered = key.lower()
    terminal = lowered.rsplit("_", 1)[-1]
    if terminal in _SENSITIVE_TERMINAL_TOKENS:
        return True
    return any(needle in lowered for needle in _SENSITIVE_SUBSTRINGS)


def strip_secret_env_lines(lines: list[str]) -> list[str]:
    """Drop sensitive assignments so ``.env`` writes never persist secrets."""
    kept: list[str] = []
    for line in lines:
        key = env_assignment_key(line)
        if key and is_sensitive_env_key(key):
            continue
        kept.append(line)
    return kept


def _ensure_no_sensitive_env_lines(lines: list[str]) -> None:
    """Fail closed when a sensitive assignment would be written to disk."""
    for line in lines:
        key = env_assignment_key(line)
        if key and is_sensitive_env_key(key):
            raise RuntimeError(
                f"Refusing to write sensitive env key {key!r} to .env; use the system keyring."
            )


def _persist_env_secret(key: str, value: str) -> bool:
    """Store a secret in the keyring. Returns False when keyring is unavailable."""
    normalized = value.strip()
    provider = next(
        (name for name, env_var in API_KEY_PROVIDER_ENVS.items() if env_var == key),
        "",
    )
    if not normalized:
        if provider:
            delete_provider_auth(provider)
        else:
            delete_keyring_secret(key)
        return True
    try:
        if provider:
            save_api_key(provider, normalized)
        else:
            save_keyring_secret(key, normalized)
    except RuntimeError:
        return False
    return True


def set_env_value(lines: list[str], key: str, value: str) -> list[str]:
    """Return ``lines`` with ``key`` assigned to ``value`` (appended when absent)."""
    if is_sensitive_env_key(key):
        raise ValueError(
            f"Refusing to write sensitive env key {key!r} to .env; use sync_env_secret()."
        )
    updated: list[str] = []
    replaced = False
    for line in lines:
        if env_assignment_key(line) != key:
            updated.append(line)
            continue
        if not replaced:
            updated.append(f"{key}={value}\n")
            replaced = True

    if not replaced:
        if updated and not updated[-1].endswith("\n"):
            updated[-1] = updated[-1] + "\n"
        updated.append(f"{key}={value}\n")
    return updated


def read_env_lines(target_path: Path) -> list[str]:
    """Read a ``.env`` file into lines, or return ``[]`` when it does not exist."""
    if not target_path.exists():
        return []
    return target_path.read_text(encoding="utf-8").splitlines(keepends=True)


def write_env_lines(target_path: Path, lines: list[str]) -> None:
    """Write non-sensitive .env lines with owner-only permissions when possible."""
    public_lines = _PublicEnvLines.from_lines(lines)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        public_lines.write_to(target_path)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write to {target_path}: permission denied. "
            "Ensure you have write access to this file, or run the command as the file owner."
        ) from exc
    if os.name != "nt":
        with suppress(OSError):
            target_path.chmod(0o600)


def sync_env_secret(key: str, value: str) -> None:
    """Persist a sensitive env value in the system keyring, not in ``.env``.

    Raises ``RuntimeError`` when the keyring backend cannot store the secret so
    callers never treat a dropped credential as a successful write.
    """
    if not is_sensitive_env_key(key):
        raise ValueError(f"{key!r} is not classified as sensitive; use sync_env_values instead.")
    if not _persist_env_secret(key, value):
        raise RuntimeError(
            f"Failed to persist {key!r} to the system keyring; "
            "secure local credential storage is unavailable."
        )


def sync_env_values(
    values: dict[str, str],
    *,
    env_path: Path | None = None,
) -> Path:
    """Write multiple non-sensitive environment values into the target .env file.

    Sensitive keys must be persisted with :func:`sync_env_secret` instead.
    Existing sensitive assignments are removed from ``.env`` whenever this file
    is rewritten so secrets do not remain in clear text.
    """
    sensitive_keys = [key for key in values if is_sensitive_env_key(key)]
    if sensitive_keys:
        joined = ", ".join(repr(key) for key in sensitive_keys)
        raise ValueError(f"Refusing to sync sensitive env keys {joined}; use sync_env_secret().")

    target_path = env_path or PROJECT_ENV_PATH
    lines = strip_secret_env_lines(read_env_lines(target_path))
    for key, value in values.items():
        lines = set_env_value(lines, key, value)

    write_env_lines(target_path, lines)
    return target_path


__all__ = [
    "PROJECT_ENV_PATH",
    "env_assignment_key",
    "is_sensitive_env_key",
    "read_env_lines",
    "set_env_value",
    "strip_secret_env_lines",
    "sync_env_secret",
    "sync_env_values",
    "write_env_lines",
]
