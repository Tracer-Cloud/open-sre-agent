"""CLI-owned LLM provider auth orchestration."""

from integrations.llm_providers.auth_profiles import (
    ProviderAuthProfile,
    iter_auth_profiles,
    resolve_auth_profile,
)
from integrations.llm_providers.auth_service import (
    AuthSetupError,
    AuthStatus,
    configure_api_key_provider,
    configure_cli_subscription_provider,
    logout_provider,
    provider_status,
)

__all__ = [
    "AuthSetupError",
    "AuthStatus",
    "ProviderAuthProfile",
    "configure_api_key_provider",
    "configure_cli_subscription_provider",
    "iter_auth_profiles",
    "logout_provider",
    "provider_status",
    "resolve_auth_profile",
]
