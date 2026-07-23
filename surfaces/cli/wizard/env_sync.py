"""Sync wizard LLM-provider choices into the project .env file.

Generic ``.env`` / keyring writing lives in :mod:`config.env_file` so every
setup surface shares one implementation. What stays here is the part that is
specific to the wizard's LLM provider model: which env keys a provider owns,
stripping the previous provider's keys on a switch, and mirroring the selection
into the wizard store.
"""

from __future__ import annotations

import os
from pathlib import Path

from config.env_file import (
    PROJECT_ENV_PATH,
    env_assignment_key,
    read_env_lines,
    set_env_value,
    sync_env_values,
    write_env_lines,
)
from config.llm_auth.auth_method import LLM_AUTH_METHOD_ENV
from surfaces.cli.wizard.config import ProviderOption


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
    # Resolve the default here rather than letting ``sync_env_values`` fall back
    # to its own: ``sync_provider_env`` below already resolves against this
    # module's ``PROJECT_ENV_PATH``, and both writers must agree on the target.
    target_path = sync_env_values(values, env_path=env_path or PROJECT_ENV_PATH)
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
        if env_assignment_key(line) == key:
            _, _, rhs = line.partition("=")
            return rhs.strip().strip("\"'") or None
    return None


def _remove_keys(lines: list[str], keys_to_remove: set[str]) -> list[str]:
    """Drop lines whose env key is in *keys_to_remove*."""
    result: list[str] = []
    for line in lines:
        key = env_assignment_key(line)
        if key and key in keys_to_remove:
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
    existing = read_env_lines(target_path)

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
    # A ``host`` credential (e.g. the Ollama host) is non-secret runtime config
    # that the wizard persists to ``.env`` — keep it as an active key so this
    # sync does not strip it back out in the same wizard run (see #3291).
    if provider.credential_kind == "host" and provider.api_key_env:
        active_non_secret.add(provider.api_key_env)
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
        lines = set_env_value(lines, key, value)

    write_env_lines(target_path, lines)

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
