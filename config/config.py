"""Global application configuration.

Clerk JWT configuration for both development and production environments.
These are public endpoints and issuer URLs, not secrets.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import get_close_matches
from enum import Enum
from typing import Literal

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
    SUPPORTED_PROVIDER_VALUES,
)
from config.local_env import bootstrap_opensre_env
from config.strict_config import StrictConfigModel


class LLMModelConfig(StrictConfigModel):
    """Configuration for an LLM provider's model variants.

    Three tiers, ordered by capability/cost:
    - ``reasoning_model`` — highest-capability model used for root-cause
      diagnosis and other deep-reasoning steps (e.g. Claude Opus, GPT-5).
    - ``classification_model`` — mid-tier model for tasks that need more
      reasoning than a fast toolcall model but don't justify reasoning cost
      (e.g. interactive-shell intent classification). Sonnet for Anthropic.
    - ``toolcall_model`` — lightweight, low-latency model for simple tool
      selection / action planning (e.g. Claude Haiku, GPT-5 mini).
    """

    reasoning_model: str
    classification_model: str
    toolcall_model: str
    max_tokens: int


class Environment(Enum):
    """Application environment."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class ClerkConfig(StrictConfigModel):
    """Clerk JWT configuration for a specific environment."""

    jwks_url: str
    issuer: str


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

# LLM Model Constants
DEFAULT_MAX_TOKENS = 4096

# Anthropic model constants
ANTHROPIC_REASONING_MODEL = "claude-opus-4-7"
ANTHROPIC_CLASSIFICATION_MODEL = "claude-sonnet-4-6"
ANTHROPIC_TOOLCALL_MODEL = "claude-haiku-4-5-20251001"

# OpenAI model constants
# Default to GPT-5.4 mini for both reasoning and toolcall paths; override via
# OPENAI_REASONING_MODEL / OPENAI_TOOLCALL_MODEL when needed.
OPENAI_REASONING_MODEL = "gpt-5.4-mini"
# Mid-tier mirrors the toolcall (mini) model by default — OpenAI's mini sits
# between full and nano, which matches the "Sonnet-equivalent" classification
# tier well enough; override via OPENAI_CLASSIFICATION_MODEL when needed.
OPENAI_CLASSIFICATION_MODEL = "gpt-5.4-mini"
OPENAI_TOOLCALL_MODEL = "gpt-5.4-mini"

# OpenRouter model constants
OPENROUTER_REASONING_MODEL = "openrouter/auto"
OPENROUTER_CLASSIFICATION_MODEL = "openrouter/auto"
OPENROUTER_TOOLCALL_MODEL = "openrouter/auto"

# DeepSeek model constants
DEEPSEEK_REASONING_MODEL = "deepseek-v4-pro"
DEEPSEEK_CLASSIFICATION_MODEL = "deepseek-v4-flash"
DEEPSEEK_TOOLCALL_MODEL = "deepseek-v4-flash"

# Gemini model constants (Google AI preview IDs; OpenAI-compatible endpoint)
# UNVERIFIED PLACEHOLDER — gemini-3.1-pro-preview / gemini-3.1-flash-lite-preview are
# forward-looking IDs that may not yet exist. Override via GEMINI_REASONING_MODEL env var.
GEMINI_REASONING_MODEL = "gemini-3.1-pro-preview"
GEMINI_CLASSIFICATION_MODEL = "gemini-3-flash-preview"
GEMINI_TOOLCALL_MODEL = "gemini-3.1-flash-lite-preview"

# NVIDIA NIM model constants
# Verified safe defaults from the NVIDIA API Catalog (build.nvidia.com).
# Override via NVIDIA_REASONING_MODEL, NVIDIA_TOOLCALL_MODEL, or NVIDIA_MODEL env vars.
NVIDIA_REASONING_MODEL = "meta/llama-3.1-405b-instruct"
NVIDIA_CLASSIFICATION_MODEL = "meta/llama-3.1-70b-instruct"
NVIDIA_TOOLCALL_MODEL = "meta/llama-3.1-8b-instruct"

# MiniMax model constants
MINIMAX_REASONING_MODEL = "MiniMax-M3"
MINIMAX_CLASSIFICATION_MODEL = "MiniMax-M2.7-highspeed"
MINIMAX_TOOLCALL_MODEL = "MiniMax-M2.7-highspeed"

# Groq model constants
GROQ_REASONING_MODEL = "llama-3.3-70b-versatile"
GROQ_CLASSIFICATION_MODEL = "llama-3.3-70b-versatile"
GROQ_TOOLCALL_MODEL = "llama-3.1-8b-instant"

# Azure OpenAI deployment-name defaults (must match your Azure deployment names).
AZURE_OPENAI_REASONING_MODEL = "gpt-5.4-mini"
AZURE_OPENAI_CLASSIFICATION_MODEL = "gpt-5.4-mini"
AZURE_OPENAI_TOOLCALL_MODEL = "gpt-5.4-mini"
DEFAULT_AZURE_OPENAI_API_VERSION = "2024-10-21"

# Base URLs for OpenAI-compatible providers
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"  # no /v1 — DeepSeek serves the OpenAI-compatible API at the root path
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Amazon Bedrock model constants (US cross-region inference profile IDs)
BEDROCK_REASONING_MODEL = "us.anthropic.claude-sonnet-4-6"
BEDROCK_CLASSIFICATION_MODEL = "us.anthropic.claude-sonnet-4-6"
BEDROCK_TOOLCALL_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Google Vertex AI model constants (Gemini models served via Vertex; ADC auth)
VERTEX_AI_REASONING_MODEL = "gemini-2.5-pro"
VERTEX_AI_CLASSIFICATION_MODEL = "gemini-2.5-flash"
VERTEX_AI_TOOLCALL_MODEL = "gemini-2.5-flash-lite"
DEFAULT_VERTEX_AI_LOCATION = "us-central1"

# Ollama local model constants
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"

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


def _llm_settings_env_payload(provider: str) -> dict[str, object]:
    """Build the raw env-backed payload used to validate LLM settings."""
    return {
        "provider": provider,
        **_llm_api_key_payload(provider),
        "anthropic_reasoning_model": os.getenv(
            "ANTHROPIC_REASONING_MODEL", ANTHROPIC_REASONING_MODEL
        ).strip()
        or ANTHROPIC_REASONING_MODEL,
        "anthropic_classification_model": os.getenv(
            "ANTHROPIC_CLASSIFICATION_MODEL", ANTHROPIC_CLASSIFICATION_MODEL
        ).strip()
        or ANTHROPIC_CLASSIFICATION_MODEL,
        "anthropic_toolcall_model": os.getenv(
            "ANTHROPIC_TOOLCALL_MODEL", ANTHROPIC_TOOLCALL_MODEL
        ).strip()
        or ANTHROPIC_TOOLCALL_MODEL,
        "openai_reasoning_model": os.getenv(
            "OPENAI_REASONING_MODEL", OPENAI_REASONING_MODEL
        ).strip()
        or OPENAI_REASONING_MODEL,
        "openai_classification_model": os.getenv(
            "OPENAI_CLASSIFICATION_MODEL", OPENAI_CLASSIFICATION_MODEL
        ).strip()
        or OPENAI_CLASSIFICATION_MODEL,
        "openai_toolcall_model": os.getenv("OPENAI_TOOLCALL_MODEL", OPENAI_TOOLCALL_MODEL).strip()
        or OPENAI_TOOLCALL_MODEL,
        "openrouter_reasoning_model": os.getenv(
            "OPENROUTER_REASONING_MODEL",
            os.getenv("OPENROUTER_MODEL", OPENROUTER_REASONING_MODEL),
        ).strip()
        or OPENROUTER_REASONING_MODEL,
        "openrouter_classification_model": os.getenv(
            "OPENROUTER_CLASSIFICATION_MODEL",
            os.getenv("OPENROUTER_MODEL", OPENROUTER_CLASSIFICATION_MODEL),
        ).strip()
        or OPENROUTER_CLASSIFICATION_MODEL,
        "openrouter_toolcall_model": os.getenv(
            "OPENROUTER_TOOLCALL_MODEL",
            os.getenv("OPENROUTER_MODEL", OPENROUTER_TOOLCALL_MODEL),
        ).strip()
        or OPENROUTER_TOOLCALL_MODEL,
        "deepseek_reasoning_model": os.getenv(
            "DEEPSEEK_REASONING_MODEL",
            os.getenv("DEEPSEEK_MODEL", DEEPSEEK_REASONING_MODEL),
        ).strip()
        or DEEPSEEK_REASONING_MODEL,
        "deepseek_classification_model": os.getenv(
            "DEEPSEEK_CLASSIFICATION_MODEL",
            os.getenv("DEEPSEEK_MODEL", DEEPSEEK_CLASSIFICATION_MODEL),
        ).strip()
        or DEEPSEEK_CLASSIFICATION_MODEL,
        "deepseek_toolcall_model": os.getenv(
            "DEEPSEEK_TOOLCALL_MODEL",
            os.getenv("DEEPSEEK_MODEL", DEEPSEEK_TOOLCALL_MODEL),
        ).strip()
        or DEEPSEEK_TOOLCALL_MODEL,
        "gemini_reasoning_model": os.getenv(
            "GEMINI_REASONING_MODEL",
            os.getenv("GEMINI_MODEL", GEMINI_REASONING_MODEL),
        ).strip()
        or GEMINI_REASONING_MODEL,
        "gemini_classification_model": os.getenv(
            "GEMINI_CLASSIFICATION_MODEL",
            os.getenv("GEMINI_MODEL", GEMINI_CLASSIFICATION_MODEL),
        ).strip()
        or GEMINI_CLASSIFICATION_MODEL,
        "gemini_toolcall_model": os.getenv(
            "GEMINI_TOOLCALL_MODEL",
            os.getenv("GEMINI_MODEL", GEMINI_TOOLCALL_MODEL),
        ).strip()
        or GEMINI_TOOLCALL_MODEL,
        "nvidia_reasoning_model": os.getenv(
            "NVIDIA_REASONING_MODEL",
            os.getenv("NVIDIA_MODEL", NVIDIA_REASONING_MODEL),
        ).strip()
        or NVIDIA_REASONING_MODEL,
        "nvidia_classification_model": os.getenv(
            "NVIDIA_CLASSIFICATION_MODEL",
            os.getenv("NVIDIA_MODEL", NVIDIA_CLASSIFICATION_MODEL),
        ).strip()
        or NVIDIA_CLASSIFICATION_MODEL,
        "nvidia_toolcall_model": os.getenv(
            "NVIDIA_TOOLCALL_MODEL",
            os.getenv("NVIDIA_MODEL", NVIDIA_TOOLCALL_MODEL),
        ).strip()
        or NVIDIA_TOOLCALL_MODEL,
        "minimax_reasoning_model": os.getenv(
            "MINIMAX_REASONING_MODEL",
            os.getenv("MINIMAX_MODEL", MINIMAX_REASONING_MODEL),
        ).strip()
        or MINIMAX_REASONING_MODEL,
        "minimax_classification_model": os.getenv(
            "MINIMAX_CLASSIFICATION_MODEL",
            os.getenv("MINIMAX_MODEL", MINIMAX_CLASSIFICATION_MODEL),
        ).strip()
        or MINIMAX_CLASSIFICATION_MODEL,
        "minimax_toolcall_model": os.getenv(
            "MINIMAX_TOOLCALL_MODEL",
            os.getenv("MINIMAX_MODEL", MINIMAX_TOOLCALL_MODEL),
        ).strip()
        or MINIMAX_TOOLCALL_MODEL,
        "groq_reasoning_model": os.getenv(
            "GROQ_REASONING_MODEL",
            os.getenv("GROQ_MODEL", GROQ_REASONING_MODEL),
        ).strip()
        or GROQ_REASONING_MODEL,
        "groq_classification_model": os.getenv(
            "GROQ_CLASSIFICATION_MODEL",
            os.getenv("GROQ_MODEL", GROQ_CLASSIFICATION_MODEL),
        ).strip()
        or GROQ_CLASSIFICATION_MODEL,
        "groq_toolcall_model": os.getenv(
            "GROQ_TOOLCALL_MODEL",
            os.getenv("GROQ_MODEL", GROQ_TOOLCALL_MODEL),
        ).strip()
        or GROQ_TOOLCALL_MODEL,
        "azure_openai_base_url": os.getenv(AZURE_OPENAI_BASE_URL_ENV, "").strip(),
        "azure_openai_api_version": os.getenv(
            AZURE_OPENAI_API_VERSION_ENV, DEFAULT_AZURE_OPENAI_API_VERSION
        ).strip()
        or DEFAULT_AZURE_OPENAI_API_VERSION,
        "azure_openai_reasoning_model": os.getenv(
            "AZURE_OPENAI_REASONING_MODEL",
            os.getenv("AZURE_OPENAI_MODEL", AZURE_OPENAI_REASONING_MODEL),
        ).strip()
        or AZURE_OPENAI_REASONING_MODEL,
        "azure_openai_classification_model": os.getenv(
            "AZURE_OPENAI_CLASSIFICATION_MODEL",
            os.getenv("AZURE_OPENAI_MODEL", AZURE_OPENAI_CLASSIFICATION_MODEL),
        ).strip()
        or AZURE_OPENAI_CLASSIFICATION_MODEL,
        "azure_openai_toolcall_model": os.getenv(
            "AZURE_OPENAI_TOOLCALL_MODEL",
            os.getenv("AZURE_OPENAI_MODEL", AZURE_OPENAI_TOOLCALL_MODEL),
        ).strip()
        or AZURE_OPENAI_TOOLCALL_MODEL,
        "bedrock_reasoning_model": os.getenv(
            "BEDROCK_REASONING_MODEL", BEDROCK_REASONING_MODEL
        ).strip()
        or BEDROCK_REASONING_MODEL,
        "bedrock_classification_model": os.getenv(
            "BEDROCK_CLASSIFICATION_MODEL", BEDROCK_CLASSIFICATION_MODEL
        ).strip()
        or BEDROCK_CLASSIFICATION_MODEL,
        "bedrock_toolcall_model": os.getenv(
            "BEDROCK_TOOLCALL_MODEL", BEDROCK_TOOLCALL_MODEL
        ).strip()
        or BEDROCK_TOOLCALL_MODEL,
        "vertex_ai_project": os.getenv("VERTEX_AI_PROJECT", "").strip(),
        "vertex_ai_location": os.getenv("VERTEX_AI_LOCATION", DEFAULT_VERTEX_AI_LOCATION).strip()
        or DEFAULT_VERTEX_AI_LOCATION,
        "vertex_ai_reasoning_model": os.getenv(
            "VERTEX_AI_REASONING_MODEL",
            os.getenv("VERTEX_AI_MODEL", VERTEX_AI_REASONING_MODEL),
        ).strip()
        or VERTEX_AI_REASONING_MODEL,
        "vertex_ai_classification_model": os.getenv(
            "VERTEX_AI_CLASSIFICATION_MODEL",
            os.getenv("VERTEX_AI_MODEL", VERTEX_AI_CLASSIFICATION_MODEL),
        ).strip()
        or VERTEX_AI_CLASSIFICATION_MODEL,
        "vertex_ai_toolcall_model": os.getenv(
            "VERTEX_AI_TOOLCALL_MODEL",
            os.getenv("VERTEX_AI_MODEL", VERTEX_AI_TOOLCALL_MODEL),
        ).strip()
        or VERTEX_AI_TOOLCALL_MODEL,
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


@dataclass(frozen=True)
class LLMResolution:
    """Outcome of resolving LLM settings for the configured provider."""

    settings: LLMSettings
    configured_provider: str
    resolved_provider: str
    attempted_providers: tuple[str, ...]
    missing_key_env: str | None

    @property
    def fell_back(self) -> bool:
        """True when the active provider differs from the configured one."""
        return self.resolved_provider != self.configured_provider

    def summary(self) -> str:
        """One-line, user-facing description of the active provider decision."""
        return f"Using configured LLM provider '{self.resolved_provider}'."


def resolve_llm_settings_verbose(
    fallback_providers: Sequence[str] = (),
) -> LLMResolution:
    """Resolve LLM settings without implicit provider fallback."""
    bootstrap_opensre_env(override=False)
    _ = fallback_providers
    configured_provider = get_configured_llm_provider()
    settings = LLMSettings.model_validate(_llm_settings_env_payload(configured_provider))
    return LLMResolution(
        settings=settings,
        configured_provider=configured_provider,
        resolved_provider=settings.provider,
        attempted_providers=(configured_provider,),
        missing_key_env=None,
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
        "fell back           : no",
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


# LLM Provider Configs
ANTHROPIC_LLM_CONFIG = LLMModelConfig(
    reasoning_model=ANTHROPIC_REASONING_MODEL,
    classification_model=ANTHROPIC_CLASSIFICATION_MODEL,
    toolcall_model=ANTHROPIC_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

OPENAI_LLM_CONFIG = LLMModelConfig(
    reasoning_model=OPENAI_REASONING_MODEL,
    classification_model=OPENAI_CLASSIFICATION_MODEL,
    toolcall_model=OPENAI_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

OPENROUTER_LLM_CONFIG = LLMModelConfig(
    reasoning_model=OPENROUTER_REASONING_MODEL,
    classification_model=OPENROUTER_CLASSIFICATION_MODEL,
    toolcall_model=OPENROUTER_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

DEEPSEEK_LLM_CONFIG = LLMModelConfig(
    reasoning_model=DEEPSEEK_REASONING_MODEL,
    classification_model=DEEPSEEK_CLASSIFICATION_MODEL,
    toolcall_model=DEEPSEEK_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

GROQ_LLM_CONFIG = LLMModelConfig(
    reasoning_model=GROQ_REASONING_MODEL,
    classification_model=GROQ_CLASSIFICATION_MODEL,
    toolcall_model=GROQ_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

AZURE_OPENAI_LLM_CONFIG = LLMModelConfig(
    reasoning_model=AZURE_OPENAI_REASONING_MODEL,
    classification_model=AZURE_OPENAI_CLASSIFICATION_MODEL,
    toolcall_model=AZURE_OPENAI_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

GEMINI_LLM_CONFIG = LLMModelConfig(
    reasoning_model=GEMINI_REASONING_MODEL,
    classification_model=GEMINI_CLASSIFICATION_MODEL,
    toolcall_model=GEMINI_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

NVIDIA_LLM_CONFIG = LLMModelConfig(
    reasoning_model=NVIDIA_REASONING_MODEL,
    classification_model=NVIDIA_CLASSIFICATION_MODEL,
    toolcall_model=NVIDIA_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

MINIMAX_LLM_CONFIG = LLMModelConfig(
    reasoning_model=MINIMAX_REASONING_MODEL,
    classification_model=MINIMAX_CLASSIFICATION_MODEL,
    toolcall_model=MINIMAX_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

BEDROCK_LLM_CONFIG = LLMModelConfig(
    reasoning_model=BEDROCK_REASONING_MODEL,
    classification_model=BEDROCK_CLASSIFICATION_MODEL,
    toolcall_model=BEDROCK_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

VERTEX_AI_LLM_CONFIG = LLMModelConfig(
    reasoning_model=VERTEX_AI_REASONING_MODEL,
    classification_model=VERTEX_AI_CLASSIFICATION_MODEL,
    toolcall_model=VERTEX_AI_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

OLLAMA_LLM_CONFIG = LLMModelConfig(
    reasoning_model=DEFAULT_OLLAMA_MODEL,
    classification_model=DEFAULT_OLLAMA_MODEL,
    toolcall_model=DEFAULT_OLLAMA_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

# Tracer API Configuration
TRACER_BASE_URL_DEV = "https://staging.tracer.cloud"
TRACER_BASE_URL_PROD = "https://app.tracer.cloud"
SLACK_CHANNEL = "tracer-rca-report-alerts"


def get_tracer_base_url() -> str:
    """Get Tracer base URL for current environment."""
    return (
        TRACER_BASE_URL_PROD if get_environment() == Environment.PRODUCTION else TRACER_BASE_URL_DEV
    )
