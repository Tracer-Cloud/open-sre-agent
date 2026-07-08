"""Azure OpenAI provider helpers for LiteLLM routing and validation."""

from __future__ import annotations

import os
from typing import Any

from core.llm.types import ModelType

AZURE_OPENAI_PROVIDER = "azure-openai"

AZURE_OPENAI_BASE_URL_ENV = "AZURE_OPENAI_BASE_URL"
AZURE_OPENAI_API_VERSION_ENV = "AZURE_OPENAI_API_VERSION"
AZURE_OPENAI_API_KEY_ENV = "AZURE_OPENAI_API_KEY"


def is_azure_openai_provider(provider: str) -> bool:
    """Return whether *provider* is the Azure OpenAI LLM slug."""
    return provider.strip().lower() == AZURE_OPENAI_PROVIDER


def normalize_azure_openai_base_url(value: str) -> str:
    """Normalize an Azure OpenAI resource endpoint URL."""
    base = (value or "").strip()
    if not base:
        return ""
    if not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    return base.rstrip("/")


def select_azure_openai_model(settings: Any, model_type: ModelType) -> str:
    """Return the configured Azure deployment name for *model_type*."""
    attr = f"azure_openai_{model_type}_model"
    return str(getattr(settings, attr))


def azure_openai_litellm_model(deployment: str) -> str:
    """Build the LiteLLM model string for an Azure deployment name."""
    name = deployment.strip()
    if name.startswith("azure/"):
        return name
    return f"azure/{name}"


def resolve_azure_openai_api_version(value: str = "") -> str:
    """Return the configured Azure API version, falling back to the OpenSRE default."""
    from config.config import DEFAULT_AZURE_OPENAI_API_VERSION

    version = (value or os.getenv(AZURE_OPENAI_API_VERSION_ENV, "")).strip()
    return version or DEFAULT_AZURE_OPENAI_API_VERSION


def azure_openai_endpoint_configured() -> bool:
    """Return True when the Azure OpenAI resource URL is present."""
    base = os.getenv(AZURE_OPENAI_BASE_URL_ENV, "").strip()
    return bool(base)


def resolve_azure_openai_request_kwargs(settings: Any, *, model_type: ModelType) -> dict[str, str]:
    """Resolve LiteLLM request fields for Azure OpenAI from runtime settings."""
    base_url = normalize_azure_openai_base_url(str(getattr(settings, "azure_openai_base_url", "")))
    api_version = resolve_azure_openai_api_version(
        str(getattr(settings, "azure_openai_api_version", ""))
    )
    if not base_url:
        raise RuntimeError(
            f"LLM provider '{AZURE_OPENAI_PROVIDER}' requires {AZURE_OPENAI_BASE_URL_ENV}."
        )
    deployment = select_azure_openai_model(settings, model_type)
    return {
        "litellm_model": azure_openai_litellm_model(deployment),
        "api_base": base_url,
        "api_version": api_version,
        "api_key_env": AZURE_OPENAI_API_KEY_ENV,
    }


__all__ = [
    "AZURE_OPENAI_API_KEY_ENV",
    "AZURE_OPENAI_API_VERSION_ENV",
    "AZURE_OPENAI_BASE_URL_ENV",
    "AZURE_OPENAI_PROVIDER",
    "azure_openai_endpoint_configured",
    "azure_openai_litellm_model",
    "is_azure_openai_provider",
    "normalize_azure_openai_base_url",
    "resolve_azure_openai_api_version",
    "resolve_azure_openai_request_kwargs",
    "select_azure_openai_model",
]
