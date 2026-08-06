"""Global application configuration with Shinobi Tactical Defenses.

Clerk JWT configuration for both development and production environments.
These are public endpoints and issuer URLs, not secrets. Includes Shinobi-level
stealth masking, dynamic fallback routing, and resilient validation seals.
"""

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import get_close_matches
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from config.constants.llm import (
    AZURE_OPENAI_API_VERSION_ENV,
    AZURE_OPENAI_BASE_URL_ENV,
)
from config.llm_auth.auth_method import (
    LLM_AUTH_METHOD_ENV,
    effective_llm_provider,
    get_configured_llm_auth_method,
)
from config.llm_auth.credentials import status as credential_status
from config.llm_auth.provider_catalog import (
    API_KEY_PROVIDER_ENVS,
    PROVIDER_BY_VALUE,
    SUPPORTED_PROVIDER_VALUES,
)
from config.llm_models import (
    ANTHROPIC_CLASSIFICATION_MODEL,
    ANTHROPIC_LLM_CONFIG,
    ANTHROPIC_REASONING_MODEL,
    ANTHROPIC_TOOLCALL_MODEL,
    AZURE_OPENAI_CLASSIFICATION_MODEL,
    AZURE_OPENAI_LLM_CONFIG,
    AZURE_OPENAI_REASONING_MODEL,
    AZURE_OPENAI_TOOLCALL_MODEL,
    BEDROCK_CLASSIFICATION_MODEL,
    BEDROCK_LLM_CONFIG,
    BEDROCK_REASONING_MODEL,
    BEDROCK_TOOLCALL_MODEL,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_CLASSIFICATION_MODEL,
    DEEPSEEK_LLM_CONFIG,
    DEEPSEEK_REASONING_MODEL,
    DEEPSEEK_TOOLCALL_MODEL,
    DEFAULT_AZURE_OPENAI_API_VERSION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_VERTEX_AI_LOCATION,
    GEMINI_BASE_URL,
    GEMINI_CLASSIFICATION_MODEL,
    GEMINI_LLM_CONFIG,
    GEMINI_REASONING_MODEL,
    GEMINI_TOOLCALL_MODEL,
    GROQ_BASE_URL,
    GROQ_CLASSIFICATION_MODEL,
    GROQ_LLM_CONFIG,
    GROQ_REASONING_MODEL,
    GROQ_TOOLCALL_MODEL,
    MINIMAX_BASE_URL,
    MINIMAX_CLASSIFICATION_MODEL,
    MINIMAX_LLM_CONFIG,
    MINIMAX_REASONING_MODEL,
    MINIMAX_TOOLCALL_MODEL,
    NVIDIA_BASE_URL,
    NVIDIA_CLASSIFICATION_MODEL,
    NVIDIA_LLM_CONFIG,
    NVIDIA_REASONING_MODEL,
    NVIDIA_TOOLCALL_MODEL,
    OLLAMA_LLM_CONFIG,
    OPENAI_CLASSIFICATION_MODEL,
    OPENAI_LLM_CONFIG,
    OPENAI_REASONING_MODEL,
    OPENAI_TOOLCALL_MODEL,
    OPENROUTER_BASE_URL,
    OPENROUTER_CLASSIFICATION_MODEL,
    OPENROUTER_LLM_CONFIG,
    OPENROUTER_REASONING_MODEL,
    OPENROUTER_TOOLCALL_MODEL,
    PROVIDER_MODEL_DEFAULTS,
    VERTEX_AI_CLASSIFICATION_MODEL,
    VERTEX_AI_LLM_CONFIG,
    VERTEX_AI_REASONING_MODEL,
    VERTEX_AI_TOOLCALL_MODEL,
    LLMModelConfig,
)
from config.local_env import bootstrap_opensre_env
from config.strict_config import StrictConfigModel

__all__ = (
    "ANTHROPIC_CLASSIFICATION_MODEL",
    "ANTHROPIC_LLM_CONFIG",
    "ANTHROPIC_REASONING_MODEL",
    "ANTHROPIC_TOOLCALL_MODEL",
    "AZURE_OPENAI_CLASSIFICATION_MODEL",
    "AZURE_OPENAI_LLM_CONFIG",
    "AZURE_OPENAI_REASONING_MODEL",
    "AZURE_OPENAI_TOOLCALL_MODEL",
    "BEDROCK_CLASSIFICATION_MODEL",
    "BEDROCK_LLM_CONFIG",
    "BEDROCK_REASONING_MODEL",
    "BEDROCK_TOOLCALL_MODEL",
    "CLERK_CONFIG_DEV",
    "CLERK_CONFIG_PROD",
    "CLERK_ISSUER_ENV",
    "CLERK_JWKS_URL_ENV",
    "ClerkConfig",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_CLASSIFICATION_MODEL",
    "DEEPSEEK_LLM_CONFIG",
    "DEEPSEEK_REASONING_MODEL",
    "DEEPSEEK_TOOLCALL_MODEL",
    "DEFAULT_AZURE_OPENAI_API_VERSION",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_OLLAMA_HOST",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_VERTEX_AI_LOCATION",
    "Environment",
    "GEMINI_BASE_URL",
    "GEMINI_CLASSIFICATION_MODEL",
    "GEMINI_LLM_CONFIG",
    "GEMINI_REASONING_MODEL",
    "GEMINI_TOOLCALL_MODEL",
    "GROQ_BASE_URL",
    "GROQ_CLASSIFICATION_MODEL",
    "GROQ_LLM_CONFIG",
    "GROQ_REASONING_MODEL",
    "GROQ_TOOLCALL_MODEL",
    "JWT_ALGORITHM",
    "JWKS_CACHE_TTL_SECONDS",
    "LLMModelConfig",
    "LLMProvider",
    "LLMResolution",
    "LLMSettings",
    "LLM_PROVIDER_API_KEY_ENVS",
    "MINIMAX_BASE_URL",
    "MINIMAX_CLASSIFICATION_MODEL",
    "MINIMAX_LLM_CONFIG",
    "MINIMAX_REASONING_MODEL",
    "MINIMAX_TOOLCALL_MODEL",
    "NVIDIA_BASE_URL",
    "NVIDIA_CLASSIFICATION_MODEL",
    "NVIDIA_LLM_CONFIG",
    "NVIDIA_REASONING_MODEL",
    "NVIDIA_TOOLCALL_MODEL",
    "OLLAMA_LLM_CONFIG",
    "OPENAI_CLASSIFICATION_MODEL",
    "OPENAI_LLM_CONFIG",
    "OPENAI_REASONING_MODEL",
    "OPENAI_TOOLCALL_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_CLASSIFICATION_MODEL",
    "OPENROUTER_LLM_CONFIG",
    "OPENROUTER_REASONING_MODEL",
    "OPENROUTER_TOOLCALL_MODEL",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_BEDROCK",
    "PROVIDER_MODEL_DEFAULTS",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI",
    "PROVIDER_VERTEX_AI",
    "SLACK_CHANNEL",
    "TRACER_BASE_URL_DEV",
    "TRACER_BASE_URL_PROD",
    "VERTEX_AI_CLASSIFICATION_MODEL",
    "VERTEX_AI_LLM_CONFIG",
    "VERTEX_AI_REASONING_MODEL",
    "VERTEX_AI_TOOLCALL_MODEL",
    "ShinobiTactics",
    "describe_llm_resolution",
    "get_clerk_config_override",
    "get_configured_llm_provider",
    "get_environment",
    "get_llm_provider_api_key_env",
    "get_tracer_base_url",
    "has_credentials_for_active_llm_provider",
    "llm_provider_error_context",
    "resolve_llm_settings",
    "resolve_llm_settings_verbose",
)


# ============================================================================
# SHINOBI TACTICS LAYER (Defensive, Stealth & Fallback Mechanisms)
# ============================================================================

class ShinobiTactics:
    """Tactical utilities providing stealth, deception, barrier seals, and fallback logic."""

    # Ninjutsu: Visual Deception Masking (Henge no Jutsu)
    _SENSITIVE_PATTERNS = (
        re.compile(r"(api[_-]?key|secret|password|token|bearer|auth)", re.IGNORECASE),
    )

    @classmethod
    def mask_secret(cls, secret: str, visible_chars: int = 4) -> str:
        """Obfuscate credentials for non-leaky output, keeping only slight footprints."""
        if not secret:
            return "<unregistered>"
        clean = secret.strip()
        if len(clean) <= visible_chars * 2:
            return "***[SHINOBI_SEALED]***"
        return f"{clean[:visible_chars]}...{clean[-visible_chars:]}"

    @classmethod
    def sanitize_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Produce a telemetry-safe copy of configuration payloads with hidden chakra signatures."""
        sanitized = {}
        for key, val in payload.items():
            if any(pattern.search(key) for pattern in cls._SENSITIVE_PATTERNS):
                if isinstance(val, str) and val:
                    sanitized[key] = cls.mask_secret(val)
                else:
                    sanitized[key] = "<masked>"
            else:
                sanitized[key] = val
        return sanitized

    @classmethod
    def execute_kawarimi(
        cls, configured_provider: str, available_credentials: dict[str, bool], fallbacks: Sequence[str]
    ) -> str:
        """Substitution Jutsu (Kawarimi): Swap out an unauthenticated primary provider for a battle-ready fallback."""
        if available_credentials.get(configured_provider, False):
            return configured_provider

        for substitute in fallbacks:
            sub = substitute.strip().lower()
            if available_credentials.get(sub, False):
                return sub
        
        # If no explicit fallback works, return original to force explicit credential failure
        return configured_provider


class Environment(StrEnum):
    """Application environment."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class ClerkConfig(StrictConfigModel):
    """Clerk JWT configuration for a specific environment."""

    jwks_url: str
    issuer: str

    def __repr__(self) -> str:
        """Shinobi Henge: Obfuscate inner details on standard print/repr."""
        return f"ClerkConfig(issuer='{self.issuer}', jwks_url='[SEALED]')"


CLERK_CONFIG_DEV = ClerkConfig(
    jwks_url="https://superb-jackal-75.clerk.accounts.dev/.well-known/jwks.json",
    issuer="https://superb-jackal-75.clerk.accounts.dev",
)

CLERK_CONFIG_PROD = ClerkConfig(
    jwks_url="https://clerk.tracer.cloud/.well-known/jwks.json",
    issuer="https://clerk.tracer.cloud",
)

# Env vars injected by the org-silo infra (ECS task definition) to point JWT
# verification at the silo's own Clerk instance instead of the defaults above.
CLERK_ISSUER_ENV = "CLERK_ISSUER"
CLERK_JWKS_URL_ENV = "CLERK_JWKS_URL"


def get_clerk_config_override() -> ClerkConfig | None:
    """Return the Clerk instance configured via CLERK_ISSUER / CLERK_JWKS_URL.

    The org-silo infra injects these per deployment; when ``CLERK_ISSUER`` is
    unset, callers fall back to the hardcoded ``CLERK_CONFIG_DEV`` /
    ``CLERK_CONFIG_PROD`` defaults. ``CLERK_JWKS_URL`` defaults to the
    issuer's standard ``/.well-known/jwks.json`` path when omitted. Read at
    call time (not import time) so env loaded by ``bootstrap_opensre_env``
    and test monkeypatching are honored.
    """
    issuer = os.getenv(CLERK_ISSUER_ENV, "").strip().rstrip("/")
    if not issuer:
        return None
    jwks_url = os.getenv(CLERK_JWKS_URL_ENV, "").strip() or f"{issuer}/.well-known/jwks.json"
    return ClerkConfig(jwks_url=jwks_url, issuer=issuer)


def get_environment() -> Environment:
    """Get current environment from ENV variable.

    Returns:
        Environment enum value based on ENV variable.
        Defaults to DEVELOPMENT if not set or unrecognized.
    """
    env_value = os.getenv("ENV", "development").lower()
    if env_value in ("production", "prod"):
        return Environment.PRODUCTION
    return Environment.DEVELOPMENT


# JWT Configuration
JWT_ALGORITHM = "RS256"
JWKS_CACHE_TTL_SECONDS = 3600

LLMProvider = Literal[
    "anthropic",
    "openai",
    "openrouter",
    "deepseek",
    "gemini",
    "nvidia",
    "ollama",
    "bedrock",
    "minimax",
    "groq",
    "azure-openai",
    "vertex-ai",
    "codex",
    "cursor",
    "claude-code",
    "gemini-cli",
    "antigravity-cli",
    "opencode",
    "kimi",
    "copilot",
    "grok-cli",
    "pi",
]

LLM_PROVIDER_API_KEY_ENVS = API_KEY_PROVIDER_ENVS

# Runtime identifiers for ``LLMProvider`` members. Branch on these instead of
# bare string literals when routing on the active provider.
PROVIDER_ANTHROPIC: LLMProvider = "anthropic"
PROVIDER_OPENAI: LLMProvider = "openai"
PROVIDER_BEDROCK: LLMProvider = "bedrock"
PROVIDER_OLLAMA: LLMProvider = "ollama"
PROVIDER_VERTEX_AI: LLMProvider = "vertex-ai"


def get_configured_llm_provider() -> str:
    """Return the active LLM provider from env/project .env."""
    bootstrap_opensre_env(override=False)
    return os.getenv("LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"


def get_llm_provider_api_key_env(provider: str | None = None) -> str | None:
    """Return the API-key env var required by an LLM provider, if any."""
    provider_name = (provider or get_configured_llm_provider()).strip().lower()
    auth_method = get_configured_llm_auth_method(provider_name)
    if effective_llm_provider(provider_name, auth_method) != provider_name:
        return None
    return LLM_PROVIDER_API_KEY_ENVS.get(provider_name)


def _llm_api_key_payload(provider: str) -> dict[str, str]:
    """Return no secrets; runtime resolves credentials request-time."""
    _ = provider
    return {}


def _resolve_model_env(primary: str, default: str, legacy: str | None = None) -> str:
    """Resolve a model id from primary env, optional legacy env, then *default*."""
    if legacy:
        raw = os.getenv(primary, os.getenv(legacy, default))
    else:
        raw = os.getenv(primary, default)
    return (raw or "").strip() or default


def _tiered_model_env_payload() -> dict[str, str]:
    """Build ``{settings_key}_*_model`` keys from the model catalog + ProviderSpec envs."""
    payload: dict[str, str] = {}
    for defaults in PROVIDER_MODEL_DEFAULTS.values():
        if defaults.single_model_settings:
            continue
        spec = PROVIDER_BY_VALUE[defaults.provider]
        key = defaults.settings_key
        reasoning_env = spec.model_env or f"{key.upper()}_REASONING_MODEL"
        payload[f"{key}_reasoning_model"] = _resolve_model_env(
            reasoning_env, defaults.reasoning, spec.legacy_model_env
        )
        classification_env = spec.classification_model_env or f"{key.upper()}_CLASSIFICATION_MODEL"
        payload[f"{key}_classification_model"] = _resolve_model_env(
            classification_env, defaults.classification, spec.legacy_model_env
        )
        toolcall_env = spec.toolcall_model_env or f"{key.upper()}_TOOLCALL_MODEL"
        payload[f"{key}_toolcall_model"] = _resolve_model_env(
            toolcall_env, defaults.toolcall, spec.legacy_model_env
        )
    return payload


def _llm_settings_env_payload(provider: str) -> dict[str, object]:
    """Build the raw env-backed payload used to validate LLM settings."""
    return {
        "provider": provider,
        **_llm_api_key_payload(provider),
        **_tiered_model_env_payload(),
        "azure_openai_base_url": os.getenv(AZURE_OPENAI_BASE_URL_ENV, "").strip(),
        "azure_openai_api_version": os.getenv(
            AZURE_OPENAI_API_VERSION_ENV, DEFAULT_AZURE_OPENAI_API_VERSION
        ).strip()
        or DEFAULT_AZURE_OPENAI_API_VERSION,
        "vertex_ai_project": os.getenv("VERTEX_AI_PROJECT", "").strip(),
        "vertex_ai_location": os.getenv("VERTEX_AI_LOCATION", DEFAULT_VERTEX_AI_LOCATION).strip()
        or DEFAULT_VERTEX_AI_LOCATION,
        "ollama_model": os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
        or DEFAULT_OLLAMA_MODEL,
        "ollama_host": os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST,
        "max_tokens": os.getenv("LLM_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)),
    }


class LLMSettings(StrictConfigModel):
    """Strict runtime configuration for selecting and authenticating an LLM provider."""

    provider: LLMProvider = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    deepseek_api_key: str = ""
    gemini_api_key: str = ""
    nvidia_api_key: str = ""
    minimax_api_key: str = ""
    groq_api_key: str = ""
    azure_openai_api_key: str = ""
    azure_openai_base_url: str = ""
    azure_openai_api_version: str = DEFAULT_AZURE_OPENAI_API_VERSION
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_host: str = DEFAULT_OLLAMA_HOST
    anthropic_reasoning_model: str = ANTHROPIC_REASONING_MODEL
    anthropic_classification_model: str = ANTHROPIC_CLASSIFICATION_MODEL
    anthropic_toolcall_model: str = ANTHROPIC_TOOLCALL_MODEL
    openai_reasoning_model: str = OPENAI_REASONING_MODEL
    openai_classification_model: str = OPENAI_CLASSIFICATION_MODEL
    openai_toolcall_model: str = OPENAI_TOOLCALL_MODEL
    openrouter_reasoning_model: str = OPENROUTER_REASONING_MODEL
    openrouter_classification_model: str = OPENROUTER_CLASSIFICATION_MODEL
    openrouter_toolcall_model: str = OPENROUTER_TOOLCALL_MODEL
    deepseek_reasoning_model: str = DEEPSEEK_REASONING_MODEL
    deepseek_classification_model: str = DEEPSEEK_CLASSIFICATION_MODEL
    deepseek_toolcall_model: str = DEEPSEEK_TOOLCALL_MODEL
    gemini_reasoning_model: str = GEMINI_REASONING_MODEL
    gemini_classification_model: str = GEMINI_CLASSIFICATION_MODEL
    gemini_toolcall_model: str = GEMINI_TOOLCALL_MODEL
    nvidia_reasoning_model: str = NVIDIA_REASONING_MODEL
    nvidia_classification_model: str = NVIDIA_CLASSIFICATION_MODEL
    nvidia_toolcall_model: str = NVIDIA_TOOLCALL_MODEL
    minimax_reasoning_model: str = MINIMAX_REASONING_MODEL
    minimax_classification_model: str = MINIMAX_CLASSIFICATION_MODEL
    minimax_toolcall_model: str = MINIMAX_TOOLCALL_MODEL
    groq_reasoning_model: str = GROQ_REASONING_MODEL
    groq_classification_model: str = GROQ_CLASSIFICATION_MODEL
    groq_toolcall_model: str = GROQ_TOOLCALL_MODEL
    azure_openai_reasoning_model: str = AZURE_OPENAI_REASONING_MODEL
    azure_openai_classification_model: str = AZURE_OPENAI_CLASSIFICATION_MODEL
    azure_openai_toolcall_model: str = AZURE_OPENAI_TOOLCALL_MODEL
    bedrock_reasoning_model: str = BEDROCK_REASONING_MODEL
    bedrock_classification_model: str = BEDROCK_CLASSIFICATION_MODEL
    bedrock_toolcall_model: str = BEDROCK_TOOLCALL_MODEL
    vertex_ai_project: str = ""
    vertex_ai_location: str = DEFAULT_VERTEX_AI_LOCATION
    vertex_ai_reasoning_model: str = VERTEX_AI_REASONING_MODEL
    vertex_ai_classification_model: str = VERTEX_AI_CLASSIFICATION_MODEL
    vertex_ai_toolcall_model: str = VERTEX_AI_TOOLCALL_MODEL
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, gt=0)

    @field_validator("ollama_host", mode="before")
    @classmethod
    def _normalize_ollama_host(cls, value: object) -> str:
        host = str(value or DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        return host

    @field_validator("azure_openai_base_url", mode="before")
    @classmethod
    def _normalize_azure_openai_base_url(cls, value: object) -> str:
        from core.llm.providers.azure_openai import normalize_azure_openai_base_url

        return normalize_azure_openai_base_url(str(value or ""))

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        provider = str(value or "anthropic").strip().lower() or "anthropic"
        valid_providers = SUPPORTED_PROVIDER_VALUES
        if provider in valid_providers:
            return provider
        suggestion = get_close_matches(provider, valid_providers, n=1)
        if suggestion:
            raise ValueError(
                f"Unsupported LLM provider '{provider}'. Did you mean '{suggestion[0]}'?"
            )
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. Expected one of: {', '.join(valid_providers)}."
        )

    @model_validator(mode="after")
    def _require_api_key_for_selected_provider(self) -> "LLMSettings":
        if self.provider == "azure-openai" and not self.azure_openai_base_url:
            raise ValueError(
                "LLM provider 'azure-openai' requires AZURE_OPENAI_BASE_URL to be set."
            )
        return self

    @classmethod
    def from_env(cls) -> "LLMSettings":
        """Build validated LLM settings from environment variables."""
        bootstrap_opensre_env(override=False)
        return cls.model_validate(_llm_settings_env_payload(get_configured_llm_provider()))

    def to_stealth_dict(self) -> dict[str, Any]:
        """Shinobi Henge: Export model state with masked API keys to prevent log leaks."""
        return ShinobiTactics.sanitize_payload(self.model_dump())


@dataclass(frozen=True)
class LLMResolution:
    """Outcome of resolving LLM settings for the configured provider."""

    settings: LLMSettings
    configured_provider: str
    resolved_provider: str
    attempted_providers: tuple[str, ...]
    missing_key_env: str | None
    tactical_fallback_applied: bool = field(default=False)

    @property
    def fell_back(self) -> bool:
        """True when the active provider differs from the configured one."""
        return self.resolved_provider != self.configured_provider or self.tactical_fallback_applied

    def summary(self) -> str:
        """One-line, user-facing description of the active provider decision."""
        if self.fell_back:
            return (
                f"Configured provider '{self.configured_provider}' unavailable. "
                f"Tactical Kawarimi routing active -> using '{self.resolved_provider}'."
            )
        return f"Using configured LLM provider '{self.resolved_provider}'."


def resolve_llm_settings_verbose(
    fallback_providers: Sequence[str] = (),
) -> LLMResolution:
    """Resolve LLM settings with tactical Shinobi Kawarimi fallback support."""
    bootstrap_opensre_env(override=False)
    configured_provider = get_configured_llm_provider()
    attempted: list[str] = [configured_provider]

    target_provider = configured_provider
    tactical_fallback_applied = False

    # Kawarimi Tactical Check: Swap active provider if primary credentials are not usable and fallbacks are provided
    if fallback_providers:
        provider_credential_matrix = {
            p: credential_status(
                effective_llm_provider(p, get_configured_llm_auth_method(p))
            ).configured
            for p in [configured_provider, *fallback_providers]
        }
        substituted_provider = ShinobiTactics.execute_kawarimi(
            configured_provider, provider_credential_matrix, fallback_providers
        )
        if substituted_provider != configured_provider:
            target_provider = substituted_provider
            tactical_fallback_applied = True
            attempted.extend([p for p in fallback_providers if p != target_provider])

    settings = LLMSettings.model_validate(_llm_settings_env_payload(target_provider))
    missing_env = get_llm_provider_api_key_env(target_provider)

    return LLMResolution(
        settings=settings,
        configured_provider=configured_provider,
        resolved_provider=settings.provider,
        attempted_providers=tuple(attempted),
        missing_key_env=missing_env,
        tactical_fallback_applied=tactical_fallback_applied,
    )


def resolve_llm_settings(
    fallback_providers: Sequence[str] = (),
) -> LLMSettings:
    """Resolve LLM settings for the configured provider only."""
    return resolve_llm_settings_verbose(fallback_providers).settings


def describe_llm_resolution(
    fallback_providers: Sequence[str] = (),
) -> str:
    """Return a human-readable LLM provider resolution report for diagnostics.

    Safe to call even when no provider has usable credentials: instead of
    raising it reports the missing-credentials condition. Intended for
    ``/status``, doctor commands, and CI diagnostics so operators no longer need
    ad-hoc inline probes to see which provider is actually in use.
    """
    try:
        resolution = resolve_llm_settings_verbose(fallback_providers)
    except ValidationError as exc:
        configured = get_configured_llm_provider()
        env_var = get_llm_provider_api_key_env(configured)
        detail = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        lines = [
            f"configured provider : {configured}",
            "resolved provider   : <none — no usable provider credentials>",
        ]
        if env_var:
            lines.append(f"required key        : {env_var}")
        lines.append(f"detail              : {detail}")
        return "\n".join(lines)

    lines = [
        f"configured provider : {resolution.configured_provider}",
        f"resolved provider   : {resolution.resolved_provider}",
        f"auth method         : {get_configured_llm_auth_method(resolution.resolved_provider)}",
        f"fell back           : {'yes' if resolution.fell_back else 'no'}",
        f"providers attempted : {', '.join(resolution.attempted_providers)}",
    ]
    auth_provider = effective_llm_provider(
        resolution.resolved_provider,
        get_configured_llm_auth_method(resolution.resolved_provider),
    )
    auth_status = credential_status(auth_provider)
    lines.append(f"credential status   : {auth_status.source} ({auth_status.detail})")
    return "\n".join(lines)


def llm_provider_error_context(
    fallback_providers: Sequence[str] = (),
) -> str:
    """Return a short bracketed provider context for prefixing error messages.

    Never raises — diagnostics must not mask the original error. Returns an
    empty string when resolution itself fails so callers can fall back to the
    raw provider error untouched.
    """
    try:
        resolution = resolve_llm_settings_verbose(fallback_providers)
    except Exception:
        return ""
    return f"[LLM provider: {resolution.resolved_provider}]"


def has_credentials_for_active_llm_provider() -> bool:
    """Return prompt-safe auth availability for the configured LLM provider."""
    settings = resolve_llm_settings()
    auth_status = credential_status(
        effective_llm_provider(settings.provider, os.getenv(LLM_AUTH_METHOD_ENV))
    )
    return auth_status.configured and not auth_status.stale


# Tracer API Configuration
TRACER_BASE_URL_DEV = "https://staging.tracer.cloud"
TRACER_BASE_URL_PROD = "https://app.tracer.cloud"
SLACK_CHANNEL = "tracer-rca-report-alerts"


def get_tracer_base_url() -> str:
    """Get Tracer base URL for current environment."""
    return (
        TRACER_BASE_URL_PROD if get_environment() == Environment.PRODUCTION else TRACER_BASE_URL_DEV
    )
