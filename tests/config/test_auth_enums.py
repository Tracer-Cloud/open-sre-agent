"""LLM-auth and secret-storage StrEnums keep their persisted string values.

The values below are written to on-disk auth metadata, analytics events, and
env vars, so they are a compatibility contract: renaming a *member* is safe, but
changing its ``.value`` breaks previously saved records. These tests pin the
values and the round-trip so a drive-by rename cannot silently move them.
"""

from __future__ import annotations

from config.llm_auth.auth_method import (
    API_KEY_AUTH_METHOD,
    OAUTH_AUTH_METHOD,
    LLMAuthMethod,
    normalize_llm_auth_method,
)
from config.llm_auth.credentials import CredentialSource
from config.llm_auth.provider_catalog import CredentialKind
from config.secrets.backend import SecretTier


def test_credential_kind_values() -> None:
    assert {kind.value for kind in CredentialKind} == {"api_key", "cli", "ambient", "local"}
    assert CredentialKind("local") is CredentialKind.LOCAL
    # StrEnum stays str-compatible for the many literal comparisons still in use.
    assert CredentialKind.API_KEY == "api_key"


def test_secret_tier_values() -> None:
    assert {tier.value for tier in SecretTier} == {"env", "keyring", "fallback", "none"}
    assert SecretTier("fallback") is SecretTier.FALLBACK
    assert SecretTier.KEYRING == "keyring"


def test_credential_source_values() -> None:
    assert {source.value for source in CredentialSource} == {
        "env",
        "keyring",
        "fallback",
        "metadata",
        "cli",
        "ambient",
        "local",
        "none",
        "unknown",
    }
    assert CredentialSource("keyring") is CredentialSource.KEYRING
    assert CredentialSource.NONE == "none"


def test_llm_auth_method_values_and_aliases() -> None:
    assert {method.value for method in LLMAuthMethod} == {"api_key", "oauth"}
    # The legacy module constants must remain the enum members callers compare against.
    assert API_KEY_AUTH_METHOD is LLMAuthMethod.API_KEY
    assert OAUTH_AUTH_METHOD is LLMAuthMethod.OAUTH


def test_normalize_llm_auth_method_round_trips_and_defaults() -> None:
    assert normalize_llm_auth_method("oauth") is LLMAuthMethod.OAUTH
    assert normalize_llm_auth_method("api_key") is LLMAuthMethod.API_KEY
    # Unknown / missing input defaults to API-key auth.
    assert normalize_llm_auth_method("nonsense") is LLMAuthMethod.API_KEY
    assert normalize_llm_auth_method(None) is LLMAuthMethod.API_KEY
