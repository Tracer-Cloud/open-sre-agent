"""Config-owned storage helpers for LLM provider auth metadata.

Credential operations are imported from :mod:`config.llm_auth.credentials`
directly, never re-exported here: this init must stay free of that module so
leaf importers (``provider_catalog``, keychain migration) never pull in
:mod:`config.secrets.store` — that edge was a cyclic-import with
``store`` → ``keychain_import`` → ``llm_auth``.
"""

from __future__ import annotations

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

__all__ = [
    "API_KEY_PROVIDER_ENVS",
    "API_KEY_AUTH_METHOD",
    "KEYLESS_PROVIDER_VALUES",
    "LLM_AUTH_METHOD_ENV",
    "OAUTH_AUTH_METHOD",
    "OAUTH_BACKEND_PROVIDER_BY_PROVIDER",
    "OAUTH_PROVIDER_BY_BACKEND_PROVIDER",
    "PROVIDER_SPECS",
    "ProviderSpec",
    "SUPPORTED_PROVIDER_VALUES",
    "canonical_llm_provider",
    "delete_provider_auth_record",
    "effective_llm_provider",
    "get_configured_llm_auth_method",
    "normalize_llm_auth_method",
    "provider_auth_record_name",
    "provider_spec",
    "resolve_provider_auth_record",
    "save_provider_auth_record",
    "supports_oauth_auth_method",
]
