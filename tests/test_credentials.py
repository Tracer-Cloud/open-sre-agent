"""Unit tests for the CredentialProvider abstraction layer."""

from __future__ import annotations

from time import time
from unittest.mock import MagicMock, patch

import pytest

from app.credentials import (
    LLM_PLATFORM_KEYS,
    CredentialProvider,
    EnvCredentialProvider,
    VaultCredentialProvider,
    build_credential_provider,
    get_current_tenant,
    get_opt,
    set_tenant_context,
)


class TestEnvCredentialProvider:
    def test_get_reads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_API_KEY", "test-datadog-key")
        provider = EnvCredentialProvider()
        assert provider.get("DD_API_KEY") == "test-datadog-key"

    def test_get_raises_key_error_for_missing_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SOME_MISSING_KEY_XYZ", raising=False)
        provider = EnvCredentialProvider()
        with pytest.raises(KeyError):
            provider.get("SOME_MISSING_KEY_XYZ")

    def test_get_raises_for_llm_platform_key(self) -> None:
        provider = EnvCredentialProvider()
        with pytest.raises(KeyError, match="LLM platform key"):
            provider.get("ANTHROPIC_API_KEY")

    def test_get_raises_for_all_llm_platform_keys(self) -> None:
        provider = EnvCredentialProvider()
        for key in LLM_PLATFORM_KEYS:
            with pytest.raises(KeyError):
                provider.get(key)


class TestGetOpt:
    def test_returns_value_when_key_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://grafana.example.com")
        assert get_opt("GRAFANA_INSTANCE_URL") == "https://grafana.example.com"

    def test_returns_empty_string_for_missing_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISSING_KEY_ABC", raising=False)
        assert get_opt("MISSING_KEY_ABC") == ""

    def test_returns_custom_default_for_missing_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISSING_KEY_ABC", raising=False)
        assert get_opt("MISSING_KEY_ABC", "my-default") == "my-default"

    def test_raises_for_llm_platform_key(self) -> None:
        with pytest.raises(KeyError, match="LLM platform key"):
            get_opt("OPENAI_API_KEY")

    def test_raises_for_llm_platform_key_ignores_default(self) -> None:
        with pytest.raises(KeyError):
            get_opt("ANTHROPIC_API_KEY", "should-not-matter")


class TestLLMPlatformKeyWhitelist:
    def test_whitelist_contains_expected_keys(self) -> None:
        assert "ANTHROPIC_API_KEY" in LLM_PLATFORM_KEYS
        assert "OPENAI_API_KEY" in LLM_PLATFORM_KEYS
        assert "OPENROUTER_API_KEY" in LLM_PLATFORM_KEYS
        assert "GEMINI_API_KEY" in LLM_PLATFORM_KEYS
        assert "NVIDIA_API_KEY" in LLM_PLATFORM_KEYS

    def test_tenant_credentials_not_in_whitelist(self) -> None:
        assert "DD_API_KEY" not in LLM_PLATFORM_KEYS
        assert "GRAFANA_READ_TOKEN" not in LLM_PLATFORM_KEYS
        assert "GITLAB_ACCESS_TOKEN" not in LLM_PLATFORM_KEYS


class TestTenantContext:
    def test_set_and_get_tenant(self) -> None:
        set_tenant_context("tenant-abc")
        assert get_current_tenant() == "tenant-abc"

    def test_get_current_tenant_raises_when_not_set(self) -> None:
        import threading

        from app.credentials import _tenant_ctx

        raised: list[bool] = []

        def _run_in_thread() -> None:
            try:
                _tenant_ctx.get()
                raised.append(False)
            except LookupError:
                raised.append(True)

        t = threading.Thread(target=_run_in_thread)
        t.start()
        t.join()
        assert raised == [True]

    def test_credential_provider_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            CredentialProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# VaultCredentialProvider unit tests (mocked boto3)
# ---------------------------------------------------------------------------

def _make_vault(region: str = "us-east-1", prefix: str = "healops") -> VaultCredentialProvider:
    """Return a VaultCredentialProvider with a mocked boto3 client."""
    mock_client = MagicMock()
    with patch("boto3.client", return_value=mock_client):
        provider = VaultCredentialProvider(region=region, prefix=prefix)
    provider._client = mock_client  # attach for assertions
    return provider


class TestVaultCredentialProviderLLMBypass:
    def test_llm_key_reads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        provider = _make_vault()
        assert provider.get("ANTHROPIC_API_KEY") == "sk-ant-test"

    def test_llm_key_never_hits_secretsmanager(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
        provider = _make_vault()
        provider.get("OPENAI_API_KEY")
        provider._client.get_secret_value.assert_not_called()

    def test_all_llm_platform_keys_bypass_vault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = _make_vault()
        for key in LLM_PLATFORM_KEYS:
            monkeypatch.setenv(key, f"dummy-{key}")
            assert provider.get(key) == f"dummy-{key}"
        provider._client.get_secret_value.assert_not_called()


class TestVaultCredentialProviderCache:
    def test_returns_value_from_secretsmanager(self) -> None:
        set_tenant_context("tenant-x")
        provider = _make_vault()
        provider._client.get_secret_value.return_value = {"SecretString": "pg-password-123"}

        result = provider.get("DB_PASSWORD")

        assert result == "pg-password-123"
        provider._client.get_secret_value.assert_called_once_with(
            SecretId="healops/tenant-x/DB_PASSWORD"
        )

    def test_second_call_within_ttl_uses_cache(self) -> None:
        set_tenant_context("tenant-cache")
        provider = _make_vault()
        provider._client.get_secret_value.return_value = {"SecretString": "cached-val"}

        provider.get("API_TOKEN")
        provider.get("API_TOKEN")

        assert provider._client.get_secret_value.call_count == 1

    def test_expired_cache_hits_secretsmanager_again(self) -> None:
        set_tenant_context("tenant-exp")
        provider = _make_vault()
        provider._client.get_secret_value.return_value = {"SecretString": "value"}

        provider.get("SOME_KEY")
        # Manually expire the cache entry.
        cache_key = ("tenant-exp", "SOME_KEY")
        provider._cache[cache_key] = ("value", time() - 1)
        provider.get("SOME_KEY")

        assert provider._client.get_secret_value.call_count == 2

    def test_secret_name_uses_prefix_and_tenant(self) -> None:
        set_tenant_context("acme-corp")
        provider = _make_vault(prefix="myapp")
        provider._client.get_secret_value.return_value = {"SecretString": "x"}

        provider.get("SLACK_TOKEN")

        provider._client.get_secret_value.assert_called_once_with(
            SecretId="myapp/acme-corp/SLACK_TOKEN"
        )


class TestVaultCredentialProviderErrors:
    def test_raises_key_error_for_missing_secret(self) -> None:
        from botocore.exceptions import ClientError

        set_tenant_context("tenant-missing")
        provider = _make_vault()
        error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}}
        provider._client.get_secret_value.side_effect = ClientError(error_response, "GetSecretValue")

        with pytest.raises(KeyError, match="DD_API_KEY"):
            provider.get("DD_API_KEY")

    def test_reraises_other_boto_errors(self) -> None:
        from botocore.exceptions import ClientError

        set_tenant_context("tenant-err")
        provider = _make_vault()
        error_response = {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}
        provider._client.get_secret_value.side_effect = ClientError(error_response, "GetSecretValue")

        with pytest.raises(ClientError):
            provider.get("SOME_KEY")


class TestBuildCredentialProvider:
    def test_returns_env_provider_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CREDENTIAL_BACKEND", raising=False)
        provider = build_credential_provider()
        assert isinstance(provider, EnvCredentialProvider)

    def test_returns_env_provider_when_backend_is_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CREDENTIAL_BACKEND", "env")
        provider = build_credential_provider()
        assert isinstance(provider, EnvCredentialProvider)

    def test_returns_vault_provider_when_backend_is_vault(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CREDENTIAL_BACKEND", "vault")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        with patch("boto3.client", return_value=MagicMock()):
            provider = build_credential_provider()
        assert isinstance(provider, VaultCredentialProvider)
