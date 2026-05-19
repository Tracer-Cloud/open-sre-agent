"""Integration tests for VaultCredentialProvider against LocalStack.

Requires a running LocalStack instance:
    docker run --rm -p 4566:4566 localstack/localstack

Run with:
    make test-vault
or:
    pytest -v -m vault_integration tests/test_vault_integration.py

The tests are skipped automatically when LocalStack is unreachable.
"""

from __future__ import annotations

import os

import pytest

LOCALSTACK_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
LOCALSTACK_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def _localstack_available() -> bool:
    try:
        import urllib.request

        urllib.request.urlopen(f"{LOCALSTACK_ENDPOINT}/_localstack/health", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.vault_integration

skip_if_no_localstack = pytest.mark.skipif(
    not _localstack_available(),
    reason="LocalStack is not running (start with: docker run --rm -p 4566:4566 localstack/localstack)",
)


@pytest.fixture(scope="module")
def secretsmanager_client():
    import boto3

    return boto3.client(
        "secretsmanager",
        region_name=LOCALSTACK_REGION,
        endpoint_url=LOCALSTACK_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


@pytest.fixture(scope="module")
def vault_provider():
    import boto3

    from app.credentials import VaultCredentialProvider

    client = boto3.client(
        "secretsmanager",
        region_name=LOCALSTACK_REGION,
        endpoint_url=LOCALSTACK_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    provider = VaultCredentialProvider(region=LOCALSTACK_REGION, prefix="healops-test")
    # Swap in the LocalStack client so VaultCredentialProvider hits LocalStack, not AWS.
    provider._client = client
    return provider


@skip_if_no_localstack
class TestVaultIntegration:
    _tenant = "localstack-tenant"
    _prefix = "healops-test"

    def _secret_name(self, key: str) -> str:
        return f"{self._prefix}/{self._tenant}/{key}"

    def test_store_and_retrieve_secret(
        self, secretsmanager_client, vault_provider
    ) -> None:
        from app.credentials import set_tenant_context

        key = "INTEGRATION_API_KEY"
        secret_name = self._secret_name(key)
        expected = "integration-secret-value-xyz"

        # Seed the secret via boto3 directly.
        try:
            secretsmanager_client.create_secret(Name=secret_name, SecretString=expected)
        except secretsmanager_client.exceptions.ResourceExistsException:
            secretsmanager_client.put_secret_value(SecretId=secret_name, SecretString=expected)

        set_tenant_context(self._tenant)
        result = vault_provider.get(key)
        assert result == expected

    def test_cache_prevents_second_network_call(
        self, secretsmanager_client, vault_provider
    ) -> None:
        from app.credentials import set_tenant_context

        key = "CACHED_API_KEY"
        secret_name = self._secret_name(key)

        try:
            secretsmanager_client.create_secret(Name=secret_name, SecretString="cached-val")
        except secretsmanager_client.exceptions.ResourceExistsException:
            secretsmanager_client.put_secret_value(SecretId=secret_name, SecretString="cached-val")

        set_tenant_context(self._tenant)
        # Clear any existing cache entry for this key.
        vault_provider._cache.pop((self._tenant, key), None)

        original_get = vault_provider._client.get_secret_value
        call_count = 0

        def counting_get(**kwargs):
            nonlocal call_count
            call_count += 1
            return original_get(**kwargs)

        vault_provider._client.get_secret_value = counting_get

        vault_provider.get(key)
        vault_provider.get(key)

        assert call_count == 1, "Expected only one network call within TTL"

    def test_missing_secret_raises_key_error(self, vault_provider) -> None:
        from app.credentials import set_tenant_context

        set_tenant_context(self._tenant)
        with pytest.raises(KeyError, match="NONEXISTENT_KEY_XYZ"):
            vault_provider.get("NONEXISTENT_KEY_XYZ")

    def test_llm_key_bypasses_vault(
        self, vault_provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.credentials import set_tenant_context

        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-ant-key")
        set_tenant_context(self._tenant)

        result = vault_provider.get("ANTHROPIC_API_KEY")
        assert result == "env-ant-key"
