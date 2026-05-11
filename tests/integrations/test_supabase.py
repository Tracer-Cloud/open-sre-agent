"""Unit tests for the Supabase integration helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.integrations.supabase import (
    SupabaseConfig,
    build_supabase_config,
    get_service_health,
    get_storage_buckets,
    resolve_supabase_config,
    supabase_config_from_env,
    supabase_extract_params,
    supabase_is_available,
    validate_supabase_config,
)

# ---------------------------------------------------------------------------
# Config construction
# ---------------------------------------------------------------------------


class TestSupabaseConfig:
    def test_is_configured_with_url_and_key(self) -> None:
        config = SupabaseConfig(url="https://abc.supabase.co", service_key="key123")
        assert config.is_configured is True

    def test_is_configured_missing_url(self) -> None:
        config = SupabaseConfig(url="", service_key="key123")
        assert config.is_configured is False

    def test_is_configured_missing_service_key(self) -> None:
        config = SupabaseConfig(url="https://abc.supabase.co", service_key="")
        assert config.is_configured is False

    def test_url_trailing_slash_stripped(self) -> None:
        config = SupabaseConfig(url="https://abc.supabase.co/", service_key="key123")
        assert not config.url.endswith("/")

    def test_headers_include_auth_and_apikey(self) -> None:
        config = SupabaseConfig(url="https://abc.supabase.co", service_key="secret")
        assert config.headers["Authorization"] == "Bearer secret"
        assert config.headers["apikey"] == "secret"

    def test_build_supabase_config_from_none(self) -> None:
        config = build_supabase_config(None)
        assert config.is_configured is False

    def test_build_supabase_config_from_dict(self) -> None:
        config = build_supabase_config({"url": "https://proj.supabase.co", "service_key": "svc"})
        assert config.url == "https://proj.supabase.co"
        assert config.service_key == "svc"


# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------


class TestSupabaseConfigFromEnv:
    def test_loads_from_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc_key")
        config = supabase_config_from_env()
        assert config is not None
        assert config.url == "https://proj.supabase.co"
        assert config.service_key == "svc_key"

    def test_returns_none_when_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc_key")
        assert supabase_config_from_env() is None

    def test_returns_none_when_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        assert supabase_config_from_env() is None

    def test_returns_none_when_both_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        assert supabase_config_from_env() is None


# ---------------------------------------------------------------------------
# resolve_supabase_config — URL origin validation (security)
# ---------------------------------------------------------------------------


class TestResolveSupabaseConfig:
    def test_resolves_matching_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
        config = resolve_supabase_config("https://proj.supabase.co")
        assert config.service_key == "svc"

    def test_rejects_mismatched_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
        with pytest.raises(ValueError, match="unrecognised host"):
            resolve_supabase_config("https://attacker.example.com")

    def test_rejects_when_env_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        with pytest.raises(ValueError, match="not configured"):
            resolve_supabase_config("https://proj.supabase.co")

    def test_resolves_from_integration_store_v2_without_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """v2 store records keep credentials under instances[0]; resolve must still work."""
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        v2_record = {
            "service": "supabase",
            "status": "active",
            "instances": [
                {
                    "name": "default",
                    "tags": {},
                    "credentials": {
                        "url": "https://proj.supabase.co",
                        "service_key": "from-store",
                    },
                }
            ],
        }
        with patch("app.integrations.store.load_integrations", return_value=[v2_record]):
            config = resolve_supabase_config("https://proj.supabase.co")
        assert config.service_key == "from-store"
        assert config.url == "https://proj.supabase.co"

    def test_strips_trailing_slash_before_comparison(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
        config = resolve_supabase_config("https://proj.supabase.co/")
        assert config.url == "https://proj.supabase.co"


# ---------------------------------------------------------------------------
# Availability and param extraction
# ---------------------------------------------------------------------------


class TestSupabaseAvailability:
    def test_is_available_with_project_url(self) -> None:
        assert supabase_is_available({"supabase": {"project_url": "https://proj.supabase.co"}})

    def test_is_available_without_project_url(self) -> None:
        assert not supabase_is_available({"supabase": {}})

    def test_is_available_missing_supabase_key(self) -> None:
        assert not supabase_is_available({})

    def test_extract_params_strips_whitespace(self) -> None:
        sources = {"supabase": {"project_url": "  https://proj.supabase.co  "}}
        params = supabase_extract_params(sources)
        assert params["project_url"] == "https://proj.supabase.co"

    def test_extract_params_empty_sources(self) -> None:
        params = supabase_extract_params({})
        assert params["project_url"] == ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateSupabaseConfig:
    def test_returns_error_when_url_missing(self) -> None:
        config = SupabaseConfig(url="", service_key="key")
        result = validate_supabase_config(config)
        assert result.ok is False
        assert "URL" in result.detail

    def test_returns_error_when_key_missing(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="")
        result = validate_supabase_config(config)
        assert result.ok is False
        assert "service key" in result.detail

    def test_returns_ok_on_200(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        with patch("app.integrations.supabase._make_request", return_value=(200, {})):
            result = validate_supabase_config(config)
        assert result.ok is True
        assert "proj.supabase.co" in result.detail

    def test_returns_error_on_non_200(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        with patch("app.integrations.supabase._make_request", return_value=(503, {})):
            result = validate_supabase_config(config)
        assert result.ok is False
        assert "503" in result.detail

    def test_returns_error_on_connection_failure(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        with patch(
            "app.integrations.supabase._make_request",
            side_effect=ConnectionError("timed out"),
        ):
            result = validate_supabase_config(config)
        assert result.ok is False
        assert "failed" in result.detail.lower()


# ---------------------------------------------------------------------------
# Service health
# ---------------------------------------------------------------------------


class TestGetServiceHealth:
    def test_returns_unavailable_when_not_configured(self) -> None:
        config = SupabaseConfig()
        result = get_service_health(config)
        assert result["available"] is False
        assert result["source"] == "supabase"

    def test_all_services_healthy(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        with patch("app.integrations.supabase._make_request", return_value=(200, {})):
            result = get_service_health(config)
        assert result["available"] is True
        assert result["overall_healthy"] is True
        assert result["degraded_services"] == []
        assert set(result["services"].keys()) == {"postgrest", "auth", "storage"}

    def test_storage_health_uses_dedicated_endpoint(self) -> None:
        """Ensure we call /storage/v1/health not the bucket-listing endpoint."""
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        called_paths: list[str] = []

        def _capture(cfg: SupabaseConfig, path: str, **_: Any) -> tuple[int, Any]:
            called_paths.append(path)
            return (200, {})

        with patch("app.integrations.supabase._make_request", side_effect=_capture):
            get_service_health(config)

        assert "/storage/v1/health" in called_paths
        assert "/storage/v1/bucket" not in called_paths

    def test_partial_degradation_reports_correct_service(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")

        def _side_effect(cfg: SupabaseConfig, path: str, **_: Any) -> tuple[int, Any]:
            return (503, {}) if path == "/auth/v1/health" else (200, {})

        with patch("app.integrations.supabase._make_request", side_effect=_side_effect):
            result = get_service_health(config)

        assert result["available"] is True
        assert result["overall_healthy"] is False
        assert "auth" in result["degraded_services"]
        assert result["services"]["postgrest"]["healthy"] is True
        assert result["services"]["auth"]["healthy"] is False

    def test_connection_error_marks_service_degraded(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        with patch(
            "app.integrations.supabase._make_request",
            side_effect=ConnectionError("refused"),
        ):
            result = get_service_health(config)
        assert result["available"] is True
        assert result["overall_healthy"] is False
        assert len(result["degraded_services"]) == 3

    def test_project_url_included_in_result(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        with patch("app.integrations.supabase._make_request", return_value=(200, {})):
            result = get_service_health(config)
        assert result["project_url"] == "https://proj.supabase.co"


# ---------------------------------------------------------------------------
# Storage buckets
# ---------------------------------------------------------------------------


class TestGetStorageBuckets:
    def test_returns_unavailable_when_not_configured(self) -> None:
        config = SupabaseConfig()
        result = get_storage_buckets(config)
        assert result["available"] is False

    def test_returns_bucket_list(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        mock_buckets = [
            {"id": "avatars", "name": "avatars", "public": True, "created_at": "2024-01-01"},
            {"id": "docs", "name": "docs", "public": False, "created_at": "2024-01-02"},
        ]
        with patch("app.integrations.supabase._make_request", return_value=(200, mock_buckets)):
            result = get_storage_buckets(config)
        assert result["available"] is True
        assert result["total_buckets"] == 2
        assert result["returned_buckets"] == 2
        assert result["truncated"] is False
        assert result["buckets"][0]["name"] == "avatars"
        assert result["buckets"][1]["public"] is False

    def test_returns_error_on_403(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        with patch("app.integrations.supabase._make_request", return_value=(403, {})):
            result = get_storage_buckets(config)
        assert result["available"] is False
        assert "403" in result["error"]

    def test_handles_non_list_body_gracefully(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        with patch(
            "app.integrations.supabase._make_request",
            return_value=(200, {"error": "unexpected"}),
        ):
            result = get_storage_buckets(config)
        assert result["available"] is True
        assert result["total_buckets"] == 0
        assert result["returned_buckets"] == 0
        assert result["truncated"] is False

    def test_handles_exception(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key")
        with patch("app.integrations.supabase._make_request", side_effect=RuntimeError("boom")):
            result = get_storage_buckets(config)
        assert result["available"] is False
        assert "boom" in result["error"]

    def test_caps_results_at_max_results(self) -> None:
        config = SupabaseConfig(url="https://proj.supabase.co", service_key="key", max_results=2)
        mock_buckets = [{"id": str(i), "name": f"bucket-{i}"} for i in range(10)]
        with patch("app.integrations.supabase._make_request", return_value=(200, mock_buckets)):
            result = get_storage_buckets(config)
        assert result["total_buckets"] == 10
        assert result["returned_buckets"] == 2
        assert result["truncated"] is True
