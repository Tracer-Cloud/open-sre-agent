"""Config-owned storage helpers for LLM provider auth metadata."""

from config.llm_auth.credentials import (
    CredentialResolution,
    CredentialSource,
    CredentialStatus,
    MissingLLMCredentialError,
    delete,
    has_api_key_env_status,
    require_for_request,
    resolve_api_key_env_for_request,
    resolve_for_request,
    save_api_key,
    source_for_api_key_env,
    status,
    verify,
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
    "CredentialResolution",
    "CredentialSource",
    "CredentialStatus",
    "KEYLESS_PROVIDER_VALUES",
    "MissingLLMCredentialError",
    "PROVIDER_SPECS",
    "ProviderSpec",
    "SUPPORTED_PROVIDER_VALUES",
    "delete",
    "delete_provider_auth_record",
    "has_api_key_env_status",
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
    "verify",
]
