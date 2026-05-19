"""Unit tests for the CredentialProvider abstraction layer."""

from __future__ import annotations

import pytest

from app.credentials import (
    LLM_PLATFORM_KEYS,
    CredentialProvider,
    EnvCredentialProvider,
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
        token = set_tenant_context("tenant-abc")
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
