"""Helpers to sync wizard choices into the project .env file."""

from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path

from app.cli.wizard.config import PROJECT_ENV_PATH, ProviderOption
from app.llm_credentials import delete_llm_api_key, has_llm_api_key, save_llm_api_key

_ENV_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
_NON_SECRET_ENV_KEYS: frozenset[str] = frozenset({"DISCORD_PUBLIC_KEY"})
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


def _is_sensitive_env_key(key: str) -> bool:
    """True when an env var should be stored in the keyring, not plain .env."""
    if key in _NON_SECRET_ENV_KEYS:
        return False
    lowered = key.lower()
    terminal = lowered.rsplit("_", 1)[-1]
    if terminal in _SENSITIVE_TERMINAL_TOKENS:
        return True
    return any(needle in lowered for needle in _SENSITIVE_SUBSTRINGS)


def _strip_sensitive_env_lines(lines: list[str]) -> list[str]:
    """Remove secret assignments so .env only carries non-sensitive config."""
    stripped: list[str] = []
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if match and _is_sensitive_env_key(match.group(1)):
            continue
        stripped.append(line)
    return stripped


def _persist_env_secret(key: str, value: str) -> bool:
    """Store a secret in the keyring. Returns False when keyring is unavailable."""
    normalized = value.strip()
    if not normalized:
        delete_llm_api_key(key)
        return True
    try:
        save_llm_api_key(key, normalized)
    except RuntimeError:
        return False
    return True


def _set_env_value(lines: list[str], key: str, value: str) -> list[str]:
    updated: list[str] = []
    replaced = False
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if not match or match.group(1) != key:
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


def _ensure_no_sensitive_env_lines(lines: list[str]) -> None:
    """Fail closed when a sensitive assignment would be written to disk."""
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if match and _is_sensitive_env_key(match.group(1)):
            raise RuntimeError(
                f"Refusing to write sensitive env key {match.group(1)!r} to .env; use the system keyring."
            )


def _write_env(target_path: Path, lines: list[str]) -> None:
    """Write non-sensitive .env lines with owner-only permissions when possible."""
    public_lines = _strip_sensitive_env_lines(lines)
    _ensure_no_sensitive_env_lines(public_lines)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("w", encoding="utf-8", newline="") as env_file:
            env_file.writelines(public_lines)  # lgtm[py/clear-text-storage-sensitive-data]
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write to {target_path}: permission denied. "
            "Ensure you have write access to this file, or run the command as the file owner."
        ) from exc
    if os.name != "nt":
        with suppress(OSError):
            target_path.chmod(0o600)


def sync_env_values(
    values: dict[str, str],
    *,
    env_path: Path | None = None,
) -> Path:
    """Write multiple environment values into the target .env file.

    Sensitive keys are persisted in the system keyring instead of plain text.
    When keyring storage is unavailable, sensitive values are not written to ``.env``.
    """
    target_path = env_path or PROJECT_ENV_PATH
    existing = (
        target_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if target_path.exists()
        else []
    )

    lines = _strip_sensitive_env_lines(existing)
    for key, value in values.items():
        if _is_sensitive_env_key(key):
            _persist_env_secret(key, value)
            lines = _remove_keys(lines, {key})
            continue
        lines = _set_env_value(lines, key, value)

    _write_env(target_path, lines)
    return target_path


def _classification_model_env(p: ProviderOption) -> str | None:
    if p.classification_model_env:
        return p.classification_model_env
    if p.model_env.endswith("_REASONING_MODEL"):
        return p.model_env.replace("_REASONING_MODEL", "_CLASSIFICATION_MODEL")
    return None


def _provider_specific_keys(p: ProviderOption) -> set[str]:
    """Return all env keys owned by a provider (api key + model keys)."""
    keys: set[str] = {p.model_env}
    if p.api_key_env:
        keys.add(p.api_key_env)
    if p.legacy_model_env:
        keys.add(p.legacy_model_env)
    if p.toolcall_model_env:
        keys.add(p.toolcall_model_env)
    classification_env = _classification_model_env(p)
    if classification_env:
        keys.add(classification_env)
    return keys


def _llm_provider_value_from_lines(lines: list[str]) -> str | None:
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if match and match.group(1) == "LLM_PROVIDER":
            _, _, rhs = line.partition("=")
            return rhs.strip().strip("\"'") or None
    return None


def _env_value_from_lines(lines: list[str], key: str) -> str | None:
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if match and match.group(1) == key:
            _, _, rhs = line.partition("=")
            return rhs.strip().strip("\"'") or None
    return None


def _remove_keys(lines: list[str], keys_to_remove: set[str]) -> list[str]:
    """Drop lines whose env key is in *keys_to_remove*."""
    result: list[str] = []
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if match and match.group(1) in keys_to_remove:
            continue
        result.append(line)
    return result


def sync_provider_env(
    *,
    provider: ProviderOption,
    model: str,
    toolcall_model: str | None = None,
    env_path: Path | None = None,
) -> Path:
    """Write non-secret provider settings into the project .env.

    Removes stale keys from other providers and API-key lines once the secret
    is in the keyring, or when switching LLM provider. If the user still has
    the active provider's key only in ``.env`` (same ``LLM_PROVIDER``), that
    line is kept until they save to the keyring.
    """
    from app.cli.wizard.config import SUPPORTED_PROVIDERS

    target_path = env_path or PROJECT_ENV_PATH
    existing = (
        target_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if target_path.exists()
        else []
    )

    # Strip every provider's API key and every provider's model keys except the
    # active provider's model slots (secrets are stored in the system keyring).
    keys_to_remove: set[str] = set()
    for p in SUPPORTED_PROVIDERS:
        keys_to_remove |= _provider_specific_keys(p)

    # Keep the active provider's model keys but always remove API key entries
    # (API keys are persisted via the system keyring, not .env).
    active_non_secret: set[str] = {provider.model_env}
    if provider.legacy_model_env:
        active_non_secret.add(provider.legacy_model_env)
    if provider.toolcall_model_env:
        active_non_secret.add(provider.toolcall_model_env)
    classification_env = _classification_model_env(provider)
    if classification_env:
        active_non_secret.add(classification_env)
    keys_to_remove -= active_non_secret

    prior_provider = _llm_provider_value_from_lines(existing)
    if (
        provider.api_key_env
        and prior_provider is not None
        and prior_provider.lower() == provider.value.lower()
        and not has_llm_api_key(provider.api_key_env)
    ):
        keys_to_remove.discard(provider.api_key_env)

    lines = _remove_keys(existing, keys_to_remove)

    values: dict[str, str] = {"LLM_PROVIDER": provider.value, provider.model_env: model}
    if provider.legacy_model_env:
        values[provider.legacy_model_env] = model
    if toolcall_model and provider.toolcall_model_env:
        values[provider.toolcall_model_env] = toolcall_model

    for key, value in values.items():
        lines = _set_env_value(lines, key, value)

    _write_env(target_path, lines)

    for key in keys_to_remove:
        os.environ.pop(key, None)
    for key in active_non_secret:
        preserved = _env_value_from_lines(lines, key)
        if preserved is not None:
            values[key] = preserved
    os.environ.update(values)

    return target_path
