"""Config-owned storage helpers for LLM provider auth metadata.

``credentials`` is loaded on first attribute access so leaf importers
(``provider_catalog``, keychain migration) never pull in
:mod:`config.secrets.store` through this package init — that edge was a
cyclic-import with ``store`` → ``keychain_import`` → ``llm_auth``.
"""

from __future__ import annotations

from typing import Any

from config.llm_auth.auth_method import (
    API_KEY_AUTH_METHOD,
    LLM_AUTH_METHOD_ENV,
    OAUTH_AUTH_METHOD,
    OAUTH_BACKEND_PROVIDER_BY_PROVIDER,
    OAUTH_PROVIDER_BY_BACKEND_PROVIDER,
    canonical_llm_provider,
    effective_llm_provider,
    get_configured_llm_auth_method,
    normalize_llm_auth_method,
    supports_oauth_auth_method,
)
from config.llm_auth.provider_catalog import (
    API_KEY_PROVIDER_ENVS,
    KEYLESS_PROVIDER_VALUES,
    PROVIDER_SPECS,
    SUPPORTED_PROVIDER_VALUES,
    ProviderSpec,
    provider_spec,
)
from config.llm_auth.records import (
    delete_provider_auth_record,
    provider_auth_record_name,
    resolve_provider_auth_record,
    save_provider_auth_record,
)

# Names resolved from :mod:`config.llm_auth.credentials` via ``__getattr__``.
_CREDENTIAL_EXPORTS: frozenset[str] = frozenset(
    {
        "CredentialResolution",
        "CredentialSource",
        "CredentialStatus",
        "MissingLLMCredentialError",
        "delete",
        "has_api_key_env_status",
        "require_for_request",
        "resolve_api_key_env_for_request",
        "resolve_for_request",
        "save_api_key",
        "source_for_api_key_env",
        "status",
        "verify",
    }
)

__all__ = [
    "API_KEY_PROVIDER_ENVS",
    "API_KEY_AUTH_METHOD",
    "CredentialResolution",
    "CredentialSource",
    "CredentialStatus",
    "KEYLESS_PROVIDER_VALUES",
    "LLM_AUTH_METHOD_ENV",
    "MissingLLMCredentialError",
    "OAUTH_AUTH_METHOD",
    "OAUTH_BACKEND_PROVIDER_BY_PROVIDER",
    "OAUTH_PROVIDER_BY_BACKEND_PROVIDER",
    "PROVIDER_SPECS",
    "ProviderSpec",
    "SUPPORTED_PROVIDER_VALUES",
    "canonical_llm_provider",
    "delete",
    "delete_provider_auth_record",
    "effective_llm_provider",
    "get_configured_llm_auth_method",
    "has_api_key_env_status",
    "normalize_llm_auth_method",
    "provider_auth_record_name",
    "provider_spec",
    "require_for_request",
    "resolve_provider_auth_record",
    "resolve_api_key_env_for_request",
    "resolve_for_request",
    "save_api_key",
    "save_provider_auth_record",
    "source_for_api_key_env",
    "status",
    "supports_oauth_auth_method",
    "verify",
]


def __getattr__(name: str) -> Any:
    if name in _CREDENTIAL_EXPORTS:
        from config.llm_auth import credentials as _credentials

        return getattr(_credentials, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
