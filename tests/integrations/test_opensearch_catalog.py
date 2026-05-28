"""Tests for OpenSearch integration classification and env loading."""

from __future__ import annotations

import pytest

from app.integrations._catalog_impl import (
    _classify_service_instance,
    resolve_effective_integrations,
)


class TestClassifyOpensearch:
    def test_classify_opensearch_with_api_key(self) -> None:
        """API key authentication is preserved (existing behavior)."""
        flat_view, key = _classify_service_instance(
            "opensearch",
            {
                "url": "https://my-cluster.com",
                "api_key": "my-api-key",
            },
            record_id="test-id",
        )

        assert key == "opensearch"
        assert flat_view is not None
        assert flat_view["url"] == "https://my-cluster.com"
        assert flat_view["api_key"] == "my-api-key"
        assert flat_view["username"] == ""
        assert flat_view["password"] == ""
        assert flat_view["integration_id"] == "test-id"

    def test_classify_opensearch_with_basic_auth(self) -> None:
        """Basic Auth credentials are forwarded to the runtime config (NEW)."""
        flat_view, key = _classify_service_instance(
            "opensearch",
            {
                "url": "https://my-cluster.com",
                "username": "admin",
                "password": "secret",
            },
            record_id="test-id",
        )

        assert key == "opensearch"
        assert flat_view is not None
        assert flat_view["url"] == "https://my-cluster.com"
        assert flat_view["api_key"] == ""
        assert flat_view["username"] == "admin"
        assert flat_view["password"] == "secret"

    def test_classify_opensearch_with_no_auth(self) -> None:
        """URL alone is sufficient for clusters with security disabled."""
        flat_view, key = _classify_service_instance(
            "opensearch",
            {"url": "https://my-cluster.com"},
            record_id="test-id",
        )

        assert key == "opensearch"
        assert flat_view is not None
        assert flat_view["url"] == "https://my-cluster.com"
        assert flat_view["api_key"] == ""
        assert flat_view["username"] == ""
        assert flat_view["password"] == ""

    def test_classify_opensearch_rejects_missing_url(self) -> None:
        """Missing URL is the only hard requirement; record is filtered out."""
        flat_view, key = _classify_service_instance(
            "opensearch",
            {"api_key": "my-api-key", "username": "admin", "password": "secret"},
            record_id="test-id",
        )

        assert flat_view is None
        assert key is None

    def test_classify_opensearch_strips_trailing_slash(self) -> None:
        """URL trailing slash is stripped for consistent downstream usage."""
        flat_view, _ = _classify_service_instance(
            "opensearch",
            {"url": "https://my-cluster.com/", "api_key": "k"},
            record_id="test-id",
        )

        assert flat_view is not None
        assert flat_view["url"] == "https://my-cluster.com"


class TestResolveEffectiveOpensearch:
    def test_resolve_effective_integrations_includes_opensearch_basic_auth_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OPENSEARCH_USERNAME/PASSWORD env vars are forwarded to the runtime (NEW)."""
        monkeypatch.setenv("OPENSEARCH_URL", "https://my-cluster.com")
        monkeypatch.setenv("OPENSEARCH_USERNAME", "admin")
        monkeypatch.setenv("OPENSEARCH_PASSWORD", "secret")

        effective = resolve_effective_integrations(store_integrations=[])

        assert "opensearch" in effective
        config = effective["opensearch"]["config"]
        assert config["url"] == "https://my-cluster.com"
        assert config["username"] == "admin"
        assert config["password"] == "secret"
        assert config["api_key"] == ""

    def test_resolve_effective_integrations_opensearch_url_only_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """URL alone is sufficient (security-disabled cluster)."""
        monkeypatch.setenv("OPENSEARCH_URL", "https://my-cluster.com")
        monkeypatch.delenv("OPENSEARCH_API_KEY", raising=False)
        monkeypatch.delenv("OPENSEARCH_USERNAME", raising=False)
        monkeypatch.delenv("OPENSEARCH_PASSWORD", raising=False)

        effective = resolve_effective_integrations(store_integrations=[])

        assert "opensearch" in effective
        config = effective["opensearch"]["config"]
        assert config["url"] == "https://my-cluster.com"
        assert config["username"] == ""
        assert config["password"] == ""
        assert config["api_key"] == ""
