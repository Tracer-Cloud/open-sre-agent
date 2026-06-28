"""Secure local storage helpers for LLM credentials."""

from __future__ import annotations

import os

from config.llm_keyring import (
    delete_llm_api_key,
    delete_llm_credential_record,
    get_keyring_setup_instructions,
    resolve_llm_api_key,
    resolve_llm_credential_record,
    save_llm_api_key,
    save_llm_credential_record,
)

__all__ = [
    "delete_llm_api_key",
    "delete_llm_credential_record",
    "get_keyring_setup_instructions",
    "resolve_env_credential",
    "resolve_llm_api_key",
    "resolve_llm_credential_record",
    "save_llm_api_key",
    "save_llm_credential_record",
]


def resolve_env_credential(env_var: str, *, default: str = "") -> str:
    """Resolve a credential from env first, then the local keychain."""
    env_value = os.getenv(env_var, default).strip()
    if env_value:
        return env_value
    return resolve_llm_api_key(env_var)
