"""Global application configuration.

Clerk JWT configuration for both development and production environments.
These are public endpoints and issuer URLs, not secrets.
"""

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import get_close_matches
from enum import Enum
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from app.llm_credentials import resolve_llm_api_key
from app.strict_config import StrictConfigModel
from app.utils.config import load_env

logger = logging.getLogger(__name__)


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
    "codex",
    "cursor",
    "claude-code",
    "gemini-cli",
    "antigravity-cli",
    "opencode",
    "kimi",
    "copilot",
    "grok-cli",
]

KEYLESS_LLM_PROVIDERS = frozenset(
    {
        "ollama",
        "bedrock",
        "codex",
        "cursor",
        "claude-code",
        "gemini-cli",
        "antigravity-cli",
        "opencode",
        "kimi",
        "copilot",
        "grok-cli",
    }
)
LLM_PROVIDER_API_KEY_ENVS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "groq": "GROQ_API_KEY",
}
DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS: tuple[str, ...] = ("openai", "anthropic")


def get_configured_llm_provider() -> str:
    """Return the active LLM provider from env/project .env."""
    load_env(override=False)
    return os.getenv("LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"


def get_llm_provider_api_key_env(provider: str | None = None) -> str | None:
    """Return the API-key env var required by an LLM provider, if any."""
    provider_name = (provider or get_configured_llm_provider()).strip().lower()
    return LLM_PROVIDER_API_KEY_ENVS.get(provider_name)


def get_llm_provider_api_key(provider: str | None = None) -> tuple[str | None, str]:
    """Resolve the API key for *provider* from env or secure local storage."""
    env_var = get_llm_provider_api_key_env(provider)
    if env_var is None:
        return None, ""
    return env_var, resolve_llm_api_key(env_var)


def _llm_settings_env_payload(provider: str) -> dict[str, object]:
    """Build the raw env-backed payload used to validate LLM settings."""
    return {
        "provider": provider,
        "anthropic_api_key": resolve_llm_api_key("ANTHROPIC_API_KEY"),
        "openai_api_key": resolve_llm_api_key("OPENAI_API_KEY"),
        "openrouter_api_key": resolve_llm_api_key("OPENROUTER_API_KEY"),
        "deepseek_api_key": resolve_llm_api_key("DEEPSEEK_API_KEY"),
        "gemini_api_key": resolve_llm_api_key("GEMINI_API_KEY"),
        "nvidia_api_key": resolve_llm_api_key("NVIDIA_API_KEY"),
        "minimax_api_key": resolve_llm_api_key("MINIMAX_API_KEY"),
        "groq_api_key": resolve_llm_api_key("GROQ_API_KEY"),
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
        "ollama_model": os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
        or DEFAULT_OLLAMA_MODEL,
        "ollama_host": os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST,
        "max_tokens": os.getenv("LLM_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)),
    }


def _candidate_llm_providers(
    configured_provider: str,
    fallback_providers: Sequence[str],
) -> tuple[str, ...]:
    """Return provider candidates in priority order without duplicates."""
    candidates: list[str] = []
    for provider in (configured_provider, *fallback_providers):
        normalized = str(provider).strip().lower()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return tuple(candidates)


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
    bedrock_reasoning_model: str = BEDROCK_REASONING_MODEL
    bedrock_classification_model: str = BEDROCK_CLASSIFICATION_MODEL
    bedrock_toolcall_model: str = BEDROCK_TOOLCALL_MODEL
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, gt=0)

    @field_validator("ollama_host", mode="before")
    @classmethod
    def _normalize_ollama_host(cls, value: object) -> str:
        host = str(value or DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        return host

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        provider = str(value or "anthropic").strip().lower() or "anthropic"
        valid_providers = (
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
            "codex",
            "cursor",
            "claude-code",
            "gemini-cli",
            "antigravity-cli",
            "opencode",
            "kimi",
            "copilot",
            "grok-cli",
        )
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
        if self.provider in KEYLESS_LLM_PROVIDERS:
            return self  # local, IAM, or CLI-provider auth is handled outside API keys
        provider_to_key = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "openrouter": self.openrouter_api_key,
            "deepseek": self.deepseek_api_key,
            "gemini": self.gemini_api_key,
            "nvidia": self.nvidia_api_key,
            "minimax": self.minimax_api_key,
            "groq": self.groq_api_key,
        }
        if provider_to_key[self.provider]:
            return self

        env_var = get_llm_provider_api_key_env(self.provider)
        raise ValueError(f"LLM provider '{self.provider}' requires {env_var} to be set.")

    @classmethod
    def from_env(cls) -> "LLMSettings":
        """Build validated LLM settings from environment variables."""
        load_env(override=False)
        return cls.model_validate(_llm_settings_env_payload(get_configured_llm_provider()))


def _is_only_missing_llm_api_key_validation(exc: ValidationError) -> bool:
    """True when the only failure is LLMSettings' missing-key model validator."""
    errors = exc.errors()
    if len(errors) != 1:
        return False
    err = errors[0]
    if err.get("type") != "value_error":
        return False
    if err.get("loc") != ():
        return False
    msg = str(err.get("msg", ""))
    return "LLM provider" in msg and "requires" in msg and "API_KEY" in msg and "to be set" in msg


@dataclass(frozen=True)
class LLMResolution:
    """Outcome of resolving usable LLM settings, with fallback diagnostics.

    :func:`resolve_llm_settings` can silently switch away from the configured
    provider when that provider is missing credentials. This record makes the
    decision observable: callers can see whether a fallback happened, which
    provider is actually being used, and why the configured one was skipped.
    Without it, a missing ``OPENAI_API_KEY`` surfaces only as a confusing
    "Anthropic credit balance too low" error even though the user configured
    OpenAI.
    """

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
        if not self.fell_back:
            return f"Using configured LLM provider '{self.resolved_provider}'."
        reason = (
            f"{self.missing_key_env} is not set" if self.missing_key_env else "it is unavailable"
        )
        hint = (
            f" Set {self.missing_key_env} or change LLM_PROVIDER to use it."
            if self.missing_key_env
            else ""
        )
        return (
            f"Configured LLM provider '{self.configured_provider}' is unusable "
            f"({reason}); falling back to '{self.resolved_provider}'.{hint}"
        )


# Deduplicates fallback warnings so the operational breadcrumb is logged once
# per distinct fallback condition instead of on every client creation.
_LLM_FALLBACK_WARNING_CACHE: set[tuple[str, str, str | None]] = set()


def reset_llm_fallback_warning_cache() -> None:
    """Clear the fallback-warning dedup cache (test/diagnostic helper)."""
    _LLM_FALLBACK_WARNING_CACHE.clear()


def _warn_on_llm_fallback(resolution: LLMResolution) -> None:
    if not resolution.fell_back:
        return
    signature = (
        resolution.configured_provider,
        resolution.resolved_provider,
        resolution.missing_key_env,
    )
    if signature in _LLM_FALLBACK_WARNING_CACHE:
        return
    _LLM_FALLBACK_WARNING_CACHE.add(signature)
    logger.warning("%s", resolution.summary())


def resolve_llm_settings_verbose(
    fallback_providers: Sequence[str] = DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS,
) -> LLMResolution:
    """Resolve usable LLM settings and report any provider fallback.

    Behaves exactly like :func:`resolve_llm_settings` but returns an
    :class:`LLMResolution` describing whether the configured provider was used
    or a fallback was chosen (and why). Emits a deduplicated warning when a
    fallback occurs so operators can see, in logs, that calls are going to a
    different provider than the one they configured.
    """
    load_env(override=False)
    configured_provider = get_configured_llm_provider()
    attempted = _candidate_llm_providers(configured_provider, fallback_providers)
    configured_missing_key_error: ValidationError | None = None

    for provider in attempted:
        try:
            settings = LLMSettings.model_validate(_llm_settings_env_payload(provider))
        except ValidationError as exc:
            if not _is_only_missing_llm_api_key_validation(exc):
                raise
            if provider == configured_provider:
                configured_missing_key_error = exc
            continue
        resolution = LLMResolution(
            settings=settings,
            configured_provider=configured_provider,
            resolved_provider=settings.provider,
            attempted_providers=attempted,
            missing_key_env=(
                get_llm_provider_api_key_env(configured_provider)
                if settings.provider != configured_provider
                else None
            ),
        )
        _warn_on_llm_fallback(resolution)
        return resolution

    if configured_missing_key_error is not None:
        raise configured_missing_key_error
    # Defensive parity with the strict path: no candidate validated for a
    # non-key reason. Re-raise the strict error semantics.
    settings = LLMSettings.from_env()
    return LLMResolution(
        settings=settings,
        configured_provider=configured_provider,
        resolved_provider=settings.provider,
        attempted_providers=attempted,
        missing_key_env=None,
    )


def resolve_llm_settings(
    fallback_providers: Sequence[str] = DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS,
) -> LLMSettings:
    """Resolve usable LLM settings from env, falling back when only the key is missing.

    ``LLMSettings.from_env`` remains strict: it validates the configured provider
    exactly. This resolver is for runtime and live-test paths that can safely use
    another hosted provider when the configured/default provider lacks credentials
    but an equivalent provider, such as OpenAI, is configured. Use
    :func:`resolve_llm_settings_verbose` when you need to know whether a fallback
    occurred.
    """
    return resolve_llm_settings_verbose(fallback_providers).settings


def describe_llm_resolution(
    fallback_providers: Sequence[str] = DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS,
) -> str:
    """Return a human-readable LLM provider resolution report for diagnostics.

    Safe to call even when no provider has usable credentials: instead of
    raising it reports the missing-credentials condition. Intended for
    ``/status``, doctor commands, and CI diagnostics so operators no longer need
    ad-hoc inline probes to see which provider is actually in use.
    """
    configured = get_configured_llm_provider()
    try:
        resolution = resolve_llm_settings_verbose(fallback_providers)
    except ValidationError as exc:
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
        f"fell back           : {'yes' if resolution.fell_back else 'no'}",
        f"providers attempted : {', '.join(resolution.attempted_providers)}",
    ]
    if resolution.fell_back and resolution.missing_key_env:
        lines.append(f"missing key         : {resolution.missing_key_env}")
    return "\n".join(lines)


def llm_provider_error_context(
    fallback_providers: Sequence[str] = DEFAULT_LLM_RESOLUTION_FALLBACK_PROVIDERS,
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
    if resolution.fell_back:
        reason = (
            f"{resolution.missing_key_env} not set"
            if resolution.missing_key_env
            else "configured provider unavailable"
        )
        return (
            f"[LLM provider: {resolution.resolved_provider} — fell back from "
            f"configured '{resolution.configured_provider}' ({reason})]"
        )
    return f"[LLM provider: {resolution.resolved_provider}]"


def has_credentials_for_active_llm_provider() -> bool:
    """Return True when the active LLM provider, or an equivalent fallback, is usable.

    Runs full LLM env validation (provider, model names, ``LLM_MAX_TOKENS``, keys via
    :func:`resolve_llm_api_key`, etc.). Callers such as synthetic tests skip only when
    validation fails solely because no acceptable provider has credentials; any other
    misconfiguration is re-raised so the run fails loudly.
    """
    try:
        resolve_llm_settings()
        return True
    except ValidationError as exc:
        if _is_only_missing_llm_api_key_validation(exc):
            return False
        raise


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
