"""Secure local storage helpers for OpenSRE secrets (LLM keys and integrations)."""

from __future__ import annotations

import os

from config.llm_keyring import (
    delete_keyring_secret,
    delete_llm_credential_record,
    get_keyring_setup_instructions,
    resolve_keyring_secret,
    resolve_llm_credential_record,
    save_keyring_secret,
    save_llm_credential_record,
)

__all__ = [
    "delete_keyring_secret",
    "delete_llm_credential_record",
    "get_keyring_setup_instructions",
    "resolve_env_credential",
    "resolve_keyring_secret",
    "resolve_llm_credential_record",
    "save_keyring_secret",
    "save_llm_credential_record",
]


def resolve_env_credential(env_var: str, *, default: str = "") -> str:
    """Resolve a credential from process env first, then the OS keyring."""
    env_value = os.getenv(env_var, default).strip()
    if env_value:
        return env_value
    return resolve_keyring_secret(env_var)
