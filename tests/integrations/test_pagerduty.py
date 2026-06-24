"""Unit tests for PagerDuty integration config, catalog, and env-var loader."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.integrations.config_models import PagerDutyIntegrationConfig


class TestPagerDutyIntegrationConfig:
    """Tests for PagerDutyIntegrationConfig model validation."""

    def test_valid_config(self) -> None:
        config = PagerDutyIntegrationConfig(api_key="test-key")
        assert config.api_key == "test-key"
        assert config.base_url == "https://api.pagerduty.com"
        assert config.integration_id == ""

    def test_custom_base_url(self) -> None:
        config = PagerDutyIntegrationConfig(api_key="key", base_url="https://custom.pagerduty.com")
        assert config.base_url == "https://custom.pagerduty.com"

    def test_api_key_required(self) -> None:
        with pytest.raises(ValidationError):
            PagerDutyIntegrationConfig()  # type: ignore[call-arg]

    def test_headers_format(self) -> None:
        config = PagerDutyIntegrationConfig(api_key="my-token")
        assert config.headers == {
            "Authorization": "Token token=my-token",
            "Content-Type": "application/json",
        }

    def test_integration_id_stored(self) -> None:
        config = PagerDutyIntegrationConfig(api_key="k", integration_id="int-123")
        assert config.integration_id == "int-123"


class TestCatalogClassify:
    """Tests for _classify_service_instance handling of 'pagerduty' key."""

    def test_classify_valid_pagerduty(self) -> None:
        from app.integrations._catalog_impl import _classify_service_instance

        config, source = _classify_service_instance(
            "pagerduty",
            {"api_key": "pd-key", "base_url": "https://api.pagerduty.com"},
            record_id="rec-1",
        )
        assert source == "pagerduty"
        assert config is not None
        assert config.api_key == "pd-key"

    def test_classify_missing_api_key_returns_none(self) -> None:
        from app.integrations._catalog_impl import _classify_service_instance

        config, source = _classify_service_instance(
            "pagerduty",
            {"api_key": "", "base_url": ""},
            record_id="rec-2",
        )
        assert config is None
        assert source is None

    def test_classify_uses_default_base_url(self) -> None:
        from app.integrations._catalog_impl import _classify_service_instance

        config, source = _classify_service_instance(
            "pagerduty",
            {"api_key": "key-1"},
            record_id="rec-3",
        )
        assert source == "pagerduty"
        assert config is not None
        assert config.base_url == "https://api.pagerduty.com"


class TestEnvLoader:
    """Tests for PAGERDUTY_API_KEY env-var loading."""

    @patch.dict("os.environ", {"PAGERDUTY_API_KEY": "env-key"}, clear=False)
    def test_env_loader_picks_up_api_key(self) -> None:
        from app.integrations._catalog_impl import load_env_integrations

        integrations = load_env_integrations()
        pd_records = [r for r in integrations if r.get("service") == "pagerduty"]
        assert len(pd_records) == 1
        assert pd_records[0]["credentials"]["api_key"] == "env-key"

    @patch.dict("os.environ", {}, clear=False)
    def test_env_loader_skips_when_no_key(self) -> None:
        import os

        os.environ.pop("PAGERDUTY_API_KEY", None)
        from app.integrations._catalog_impl import load_env_integrations

        integrations = load_env_integrations()
        pd_records = [r for r in integrations if r.get("service") == "pagerduty"]
        assert len(pd_records) == 0
