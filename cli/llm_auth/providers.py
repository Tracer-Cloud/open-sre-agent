"""Provider metadata for browser-assisted LLM auth setup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cli.wizard.config import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS, ProviderOption

AuthKind = Literal["api_key", "cli_subscription"]


@dataclass(frozen=True)
class ProviderAuthProfile:
    """User-facing auth metadata for one provider setup path."""

    name: str
    provider_value: str
    label: str
    kind: AuthKind
    aliases: tuple[str, ...] = ()
    setup_url: str | None = None
    auth_hint: str = ""

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.name, self.provider_value, *self.aliases)


_API_KEY_SETUP_URLS: dict[str, str] = {
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
    "openrouter": "https://openrouter.ai/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "gemini": "https://aistudio.google.com/app/apikey",
    "nvidia": "https://build.nvidia.com/",
    "minimax": "https://intl.minimaxi.com/user-center/basic-information/interface-key",
    "groq": "https://console.groq.com/keys",
}


_SUBSCRIPTION_PROFILES: tuple[ProviderAuthProfile, ...] = (
    ProviderAuthProfile(
        name="chatgpt",
        provider_value="codex",
        label="ChatGPT subscription via Codex CLI",
        kind="cli_subscription",
        aliases=("openai-chatgpt", "openai-codex", "codex-cli"),
        setup_url="https://github.com/openai/codex",
        auth_hint="Run: codex login",
    ),
    ProviderAuthProfile(
        name="claude",
        provider_value="claude-code",
        label="Anthropic subscription via Claude Code CLI",
        kind="cli_subscription",
        aliases=(
            "claude-ai",
            "claude-subscription",
            "anthropic-subscription",
            "anthropic-claude",
            "claude-code-cli",
        ),
        setup_url="https://github.com/anthropics/claude-code",
        auth_hint="Run: claude auth login",
    ),
)


def _api_key_profiles() -> tuple[ProviderAuthProfile, ...]:
    profiles: list[ProviderAuthProfile] = []
    for provider in SUPPORTED_PROVIDERS:
        if provider.credential_kind != "api_key":
            continue
        profiles.append(
            ProviderAuthProfile(
                name=provider.value,
                provider_value=provider.value,
                label=provider.label
                if provider.label.lower().endswith("api key")
                else f"{provider.label} API key",
                kind="api_key",
                setup_url=_API_KEY_SETUP_URLS.get(provider.value),
                auth_hint=f"Paste {provider.api_key_env}",
            )
        )
    return tuple(profiles)


def iter_auth_profiles() -> tuple[ProviderAuthProfile, ...]:
    """Return all supported auth setup paths."""
    return (*_SUBSCRIPTION_PROFILES, *_api_key_profiles())


def resolve_auth_profile(raw_name: str) -> ProviderAuthProfile:
    """Resolve a user-supplied provider/auth alias to an auth profile."""
    normalized = raw_name.strip().lower()
    if not normalized:
        raise KeyError(raw_name)
    for profile in iter_auth_profiles():
        if normalized in {name.lower() for name in profile.all_names}:
            return profile
    raise KeyError(raw_name)


def provider_for_profile(profile: ProviderAuthProfile) -> ProviderOption:
    """Return the wizard provider option for an auth profile."""
    return PROVIDER_BY_VALUE[profile.provider_value]


__all__ = [
    "AuthKind",
    "ProviderAuthProfile",
    "iter_auth_profiles",
    "provider_for_profile",
    "resolve_auth_profile",
]
