"""Helpers to sync wizard choices into the project .env file."""

from __future__ import annotations

import os
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from config.llm_auth.auth_method import LLM_AUTH_METHOD_ENV
from config.llm_auth.credentials import delete as delete_provider_auth
from config.llm_auth.credentials import save_api_key
from config.llm_auth.provider_catalog import API_KEY_PROVIDER_ENVS
from config.llm_credentials import delete_llm_api_key, save_llm_api_key
from surfaces.cli.wizard.config import PROJECT_ENV_PATH, ProviderOption

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


@dataclass(frozen=True)
class _PublicEnvLines:
    """Validated `.env` content that contains no sensitive assignments."""

    lines: tuple[str, ...]

    @classmethod
    def from_lines(cls, lines: list[str]) -> _PublicEnvLines:
        public_lines = _strip_sensitive_env_lines(lines)
        _ensure_no_sensitive_env_lines(public_lines)
        return cls(tuple(public_lines))

    def write_to(self, target_path: Path) -> None:
        with target_path.open("w", encoding="utf-8", newline="") as env_file:
            env_file.writelines(self.lines)


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


def _strip_keyring_backed_secret_lines(lines: list[str]) -> list[str]:
    """Drop sensitive assignments so `.env` writes never persist secrets."""
    kept: list[str] = []
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if match and _is_sensitive_env_key(match.group(1)):
            continue
        kept.append(line)
    return kept


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
            delete_llm_api_key(key)
        return True
    try:
        if provider:
            save_api_key(provider, normalized)
        else:
            save_llm_api_key(key, normalized)
    except RuntimeError:
        return False
    return True


def _set_env_value(lines: list[str], key: str, value: str) -> list[str]:
    if _is_sensitive_env_key(key):
        raise RuntimeError(
            f"Refusing to write sensitive env key {key!r} to .env; use sync_env_secret()."
        )
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


def _write_env_lines(target_path: Path, lines: list[str]) -> None:
    """Write merged env lines after rejecting sensitive assignments."""
    _write_env(target_path, lines)


def sync_env_secret(key: str, value: str) -> None:
    """Persist a sensitive env value in the system keyring, not in ``.env``."""
    if not _is_sensitive_env_key(key):
        raise ValueError(f"{key!r} is not classified as sensitive; use sync_env_values instead.")
    _persist_env_secret(key, value)


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
    sensitive_keys = [key for key in values if _is_sensitive_env_key(key)]
    if sensitive_keys:
        joined = ", ".join(repr(key) for key in sensitive_keys)
        raise ValueError(f"Refusing to sync sensitive env keys {joined}; use sync_env_secret().")

    target_path = env_path or PROJECT_ENV_PATH
    existing = (
        target_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if target_path.exists()
        else []
    )

    lines = _strip_keyring_backed_secret_lines(list(existing))
    for key, value in values.items():
        lines = _set_env_value(lines, key, value)

    _write_env_lines(target_path, lines)
    return target_path


def sync_reasoning_model_env(
    *,
    provider: ProviderOption,
    model: str,
    env_path: Path | None = None,
) -> Path:
    """Write reasoning model env vars to ``.env``, update runtime env, and sync wizard store."""
    values: dict[str, str] = {provider.model_env: model}
    if provider.legacy_model_env:
        values[provider.legacy_model_env] = model
    target_path = sync_env_values(values, env_path=env_path)
    os.environ.update(values)
    _sync_llm_selection_to_store(provider=provider, model=model)
    return target_path


def _sync_llm_selection_to_store(
    *,
    provider: ProviderOption,
    model: str,
    model_provider: ProviderOption | None = None,
    auth_method: str | None = None,
) -> None:
    from surfaces.cli.wizard.store import update_local_llm_selection

    resolved_model_provider = model_provider or provider
    update_local_llm_selection(
        provider=provider.value,
        model=model,
        api_key_env=provider.api_key_env or "",
        model_env=resolved_model_provider.model_env,
        auth_method=auth_method,
    )


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
    if p.endpoint_env:
        keys.add(p.endpoint_env)
    if p.api_version_env:
        keys.add(p.api_version_env)
    classification_env = _classification_model_env(p)
    if classification_env:
        keys.add(classification_env)
    return keys


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
    model_provider: ProviderOption | None = None,
    auth_method: str | None = None,
    extra_env: dict[str, str] | None = None,
    env_path: Path | None = None,
) -> Path:
    """Write non-secret provider settings into the project .env.

    Removes stale keys from other providers and every API-key line. Secrets are
    stored in the system keyring, not in ``.env``.
    """
    from surfaces.cli.wizard.config import SUPPORTED_PROVIDERS

    resolved_model_provider = model_provider or provider
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

    keys_to_remove.add(LLM_AUTH_METHOD_ENV)
    from core.llm.transport_mode import LLM_TRANSPORT_ENV

    keys_to_remove.add(LLM_TRANSPORT_ENV)

    # Keep the active provider's model keys but always remove API key entries
    # (API keys are persisted via the system keyring, not .env).
    active_non_secret: set[str] = {resolved_model_provider.model_env}
    if resolved_model_provider.legacy_model_env:
        active_non_secret.add(resolved_model_provider.legacy_model_env)
    if resolved_model_provider.toolcall_model_env:
        active_non_secret.add(resolved_model_provider.toolcall_model_env)
    classification_env = _classification_model_env(resolved_model_provider)
    if classification_env:
        active_non_secret.add(classification_env)
    if provider.value == "azure-openai":
        if provider.endpoint_env:
            active_non_secret.add(provider.endpoint_env)
        if provider.api_version_env:
            active_non_secret.add(provider.api_version_env)
    keys_to_remove -= active_non_secret

    lines = _remove_keys(existing, keys_to_remove)

    values: dict[str, str] = {
        "LLM_PROVIDER": provider.value,
        resolved_model_provider.model_env: model,
    }
    if auth_method:
        values[LLM_AUTH_METHOD_ENV] = auth_method
    if resolved_model_provider.legacy_model_env:
        values[resolved_model_provider.legacy_model_env] = model
    if toolcall_model and resolved_model_provider.toolcall_model_env:
        values[resolved_model_provider.toolcall_model_env] = toolcall_model
    if provider.value == "azure-openai":
        values[LLM_TRANSPORT_ENV] = "litellm"
        if provider.api_version_env:
            from core.llm.providers.azure_openai import resolve_azure_openai_api_version

            values[provider.api_version_env] = resolve_azure_openai_api_version()
        if provider.endpoint_env:
            preserved_base = (
                _env_value_from_lines(lines, provider.endpoint_env)
                or os.getenv(provider.endpoint_env, "").strip()
            )
            if preserved_base:
                values[provider.endpoint_env] = preserved_base
    if extra_env:
        values.update(extra_env)

    for key, value in values.items():
        lines = _set_env_value(lines, key, value)

    _write_env_lines(target_path, lines)

    for key in keys_to_remove:
        os.environ.pop(key, None)
    for key in active_non_secret:
        preserved = _env_value_from_lines(lines, key)
        if preserved is not None:
            values[key] = preserved
    os.environ.update(values)
    _sync_llm_selection_to_store(
        provider=provider,
        model=model,
        model_provider=resolved_model_provider,
        auth_method=auth_method,
    )

    return target_path
