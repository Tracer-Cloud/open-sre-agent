"""CLI-owned LLM provider auth orchestration."""

from cli.llm_auth.providers import (
    ProviderAuthProfile,
    iter_auth_profiles,
    resolve_auth_profile,
)
from cli.llm_auth.service import (
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
