"""Secure local storage helpers for OpenSRE secrets (LLM keys and integrations)."""

from __future__ import annotations

import os

from config.llm_keyring import (
    delete_fallback_secret,
    delete_keyring_secret,
    delete_llm_credential_record,
    get_keyring_setup_instructions,
    resolve_fallback_secret,
    resolve_keyring_secret,
    resolve_llm_credential_record,
    resolve_secret_with_fallback,
    save_fallback_secret,
    save_keyring_secret,
    save_llm_credential_record,
    save_secret_with_fallback,
)

__all__ = [
    "delete_fallback_secret",
    "delete_keyring_secret",
    "delete_llm_credential_record",
    "get_keyring_setup_instructions",
    "resolve_env_credential",
    "resolve_fallback_secret",
    "resolve_keyring_secret",
    "resolve_llm_credential_record",
    "resolve_secret_with_fallback",
    "save_fallback_secret",
    "save_keyring_secret",
    "save_llm_credential_record",
    "save_secret_with_fallback",
]


def resolve_env_credential(env_var: str, *, default: str = "") -> str:
    """Resolve a credential from process env, then the OS keyring, then the
    local fallback store (used when onboarding saved a secret without a
    working keyring backend; see #1403, #3348)."""
    env_value = os.getenv(env_var, default).strip()
    if env_value:
        return env_value
    return resolve_secret_with_fallback(env_var)
