"""Keyring-backed metadata records for LLM provider auth."""

from __future__ import annotations

from datetime import UTC, datetime

from config.llm_credentials import (
    delete_llm_credential_record,
    resolve_llm_credential_record,
    save_llm_credential_record,
)

_AUTH_RECORD_PREFIX = "provider-auth:"


def provider_auth_record_name(provider: str) -> str:
    """Return the keyring record name for one LLM provider auth status record."""
    normalized = provider.strip().lower()
    if not normalized:
        raise ValueError("provider must not be empty")
    return f"{_AUTH_RECORD_PREFIX}{normalized}"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def save_provider_auth_record(
    *,
    provider: str,
    auth_name: str,
    kind: str,
    source: str,
    detail: str,
) -> None:
    """Persist non-token auth metadata for a provider."""
    provider_value = provider.strip().lower()
    save_llm_credential_record(
        provider_auth_record_name(provider_value),
        {
            "provider": provider_value,
            "auth_name": auth_name,
            "kind": kind,
            "source": source,
            "detail": detail,
            "updated_at": _utc_now(),
        },
    )


def resolve_provider_auth_record(provider: str) -> dict[str, str]:
    """Resolve non-token auth metadata for a provider."""
    return resolve_llm_credential_record(provider_auth_record_name(provider))


def delete_provider_auth_record(provider: str) -> None:
    """Delete non-token auth metadata for a provider."""
    delete_llm_credential_record(provider_auth_record_name(provider))


__all__ = [
    "delete_provider_auth_record",
    "provider_auth_record_name",
    "resolve_provider_auth_record",
    "save_provider_auth_record",
]
