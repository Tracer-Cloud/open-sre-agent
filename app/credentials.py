"""Tenant-aware credential provider abstraction for multi-tenancy.

Replaces direct os.getenv() credential reads with a swappable provider.
LLM platform keys are explicitly excluded — they stay in os.environ only.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from contextvars import ContextVar, Token
from time import time
from typing import Final

_tenant_ctx: ContextVar[str] = ContextVar("tenant_id")

# LLM platform keys are platform-owned, not tenant-scoped. Attempting to
# read them via CredentialProvider raises KeyError to prevent misuse.
LLM_PLATFORM_KEYS: Final[frozenset[str]] = frozenset({
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "REQUESTY_API_KEY",
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
    "MINIMAX_API_KEY",
    "KIMI_API_KEY",
    "CURSOR_API_KEY",
    "BEDROCK_ACCESS_KEY_ID",
    "BEDROCK_SECRET_ACCESS_KEY",
})

CREDENTIAL_BACKEND: str = os.environ.get("CREDENTIAL_BACKEND", "env")


def get_current_tenant() -> str:
    """Return the tenant ID for the current async context.

    Raises LookupError if no tenant has been set (e.g. outside a request).
    """
    return _tenant_ctx.get()


def set_tenant_context(tenant_id: str) -> Token[str]:
    """Set the tenant ID for the current async context. Returns the Token."""
    return _tenant_ctx.set(tenant_id)


class CredentialProvider(ABC):
    @abstractmethod
    def get(self, key: str) -> str: ...


class EnvCredentialProvider(CredentialProvider):
    """Single-tenant provider that reads from os.environ (local dev default)."""

    def get(self, key: str) -> str:
        if key in LLM_PLATFORM_KEYS:
            raise KeyError(
                f"{key!r} is an LLM platform key; use resolve_llm_api_key() instead"
            )
        return os.environ[key]


class VaultCredentialProvider(CredentialProvider):
    """Multi-tenant provider that fetches per-tenant credentials from AWS Secrets Manager.

    Secret naming convention: ``{prefix}/{tenant_id}/{CREDENTIAL_KEY}``

    LLM platform keys bypass the vault and are always read directly from the
    process environment — they are platform-owned, never tenant-scoped.
    """

    def __init__(self, region: str, prefix: str = "healops", ttl: int = 300) -> None:
        import boto3  # lazy import so non-vault deployments don't require boto3

        self._client = boto3.client("secretsmanager", region_name=region)
        self._prefix = prefix
        self._ttl = ttl
        # (tenant_id, key) -> (value, expiry_timestamp)
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}

    def get(self, key: str) -> str:
        # LLM platform keys are always resolved from the process environment.
        if key in LLM_PLATFORM_KEYS:
            return os.environ[key]

        tenant_id = get_current_tenant()
        cache_key = (tenant_id, key)

        cached = self._cache.get(cache_key)
        if cached is not None:
            value, expiry = cached
            if time() < expiry:
                return value

        secret_name = f"{self._prefix}/{tenant_id}/{key}"
        try:
            from botocore.exceptions import ClientError

            try:
                resp = self._client.get_secret_value(SecretId=secret_name)
                value = resp["SecretString"]
                self._cache[cache_key] = (value, time() + self._ttl)
                return value
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                    raise KeyError(
                        f"Credential {key!r} not found for tenant {tenant_id!r}"
                    ) from exc
                raise
        except ImportError:
            # botocore not available — fall through and let the raw error propagate.
            resp = self._client.get_secret_value(SecretId=secret_name)
            value = resp["SecretString"]
            self._cache[cache_key] = (value, time() + self._ttl)
            return value


def build_credential_provider() -> CredentialProvider:
    """Instantiate the correct provider from environment variables.

    ``CREDENTIAL_BACKEND=vault`` activates :class:`VaultCredentialProvider`.
    ``AWS_REGION`` (or ``VAULT_REGION``) and ``VAULT_PREFIX`` are honoured when
    building the vault provider.  Falls back to :class:`EnvCredentialProvider`.
    """
    backend = os.environ.get("CREDENTIAL_BACKEND", "env").strip().lower()
    if backend == "vault":
        region = (
            os.environ.get("VAULT_REGION")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        )
        prefix = os.environ.get("VAULT_PREFIX", "healops")
        return VaultCredentialProvider(region=region, prefix=prefix)
    return EnvCredentialProvider()


# Module-level singleton — swapped out at startup for multi-tenant backends.
# Call build_credential_provider() at application startup (see app/config.py)
# and reassign this name to activate the correct backend.
credential_provider: CredentialProvider = build_credential_provider()


def get_opt(key: str, default: str = "") -> str:
    """Get a tenant credential, returning *default* when the key is absent.

    Use this for optional integration credentials where absence means
    "not configured". LLM platform keys always raise regardless of default.
    """
    if key in LLM_PLATFORM_KEYS:
        raise KeyError(
            f"{key!r} is an LLM platform key; use resolve_llm_api_key() instead"
        )
    try:
        return credential_provider.get(key)
    except KeyError:
        return default
