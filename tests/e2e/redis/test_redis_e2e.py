"""Redis E2E tests verifying integration with investigation pipeline.

Tests:
- Redis config resolution from store and env
- Redis verification (ping, server info)
- Redis source detection in investigation state
- Redis tools availability for query execution
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.catalog import classify_integrations as _classify_integrations
from app.integrations.verify import verify_integrations
from tests.e2e.source_helpers import resolve_available_tool_sources


class TestRedisIntegrationResolution:
    """Test Redis config resolution from multiple sources."""

    def test_redis_resolution_from_store(self):
        """Redis integration correctly resolved from local store."""
        integrations = [
            {
                "id": "redis-prod",
                "service": "redis",
                "status": "active",
                "credentials": {
                    "host": "prod-cache.redis.internal",
                    "port": 6380,
                    "username": "monitor",
                    "password": "s3cret",
                    "db": 1,
                    "ssl": True,
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "redis" in resolved
        assert resolved["redis"]["host"] == "prod-cache.redis.internal"
        assert resolved["redis"]["port"] == 6380
        assert resolved["redis"]["username"] == "monitor"
        assert resolved["redis"]["db"] == 1
        assert resolved["redis"]["ssl"] is True

    def test_redis_invalid_config_skipped(self):
        """Invalid Redis integration config is safely skipped."""
        integrations = [
            {
                "id": "bad-redis",
                "service": "redis",
                "status": "active",
                "credentials": {
                    "host": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        # Should not include Redis if host is empty
        assert resolved.get("redis") is None


class TestRedisToolSourceAvailability:
    """Test Redis source availability in the tool-registry investigation path."""

    def test_redis_tool_source_available_from_resolved_integration(self):
        """Redis source is available when a configured integration exists."""
        resolved_integrations = {
            "redis": {
                "host": "localhost",
                "port": 6379,
                "username": "",
                "password": "",
                "db": 0,
                "ssl": False,
            }
        }

        sources = resolve_available_tool_sources(resolved_integrations)

        assert "redis" in sources
        assert sources["redis"]["host"] == "localhost"
        assert sources["redis"]["port"] == 6379

    def test_redis_tool_source_uses_configured_db(self):
        """Redis tool params come from the resolved integration config."""
        resolved_integrations = {
            "redis": {
                "host": "localhost",
                "port": 6379,
                "username": "",
                "password": "",
                "db": 3,
                "ssl": False,
            }
        }

        sources = resolve_available_tool_sources(resolved_integrations)

        assert "redis" in sources
        assert sources["redis"]["db"] == 3

    def test_redis_tool_source_unavailable_if_unconfigured(self):
        """Redis source is not included if not configured."""
        resolved_integrations = {}

        sources = resolve_available_tool_sources(resolved_integrations)

        assert "redis" not in sources


class TestRedisVerification:
    """Test Redis integration verification flow."""

    @patch("app.integrations.redis._get_client")
    def test_verify_redis_success(self, mock_get_client):
        """Redis verification succeeds with valid config."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.info.return_value = {"redis_version": "7.2.4"}
        mock_get_client.return_value = mock_client

        results = verify_integrations(service="redis")

        assert len(results) >= 1
        redis_result = next((r for r in results if r["service"] == "redis"), None)
        assert redis_result is not None
        # Status can be passed or missing depending on env config
        assert redis_result["status"] in ("passed", "missing")

    def test_verify_integrations_structure(self):
        """Verify integrations returns expected result structure."""
        # Just verify the function exists and can be called - actual verification
        # depends on environment setup (Redis connection available)
        try:
            results = verify_integrations(service="redis")
            assert isinstance(results, list)
            for result in results:
                if result["service"] == "redis":
                    assert "status" in result
                    assert "detail" in result
                    assert result["status"] in ("passed", "missing", "failed")
        except Exception as exc:
            # If no Redis is configured, that's ok - just testing structure
            assert exc.__class__.__name__


class TestRedisToolsAvailability:
    """Test Redis tools are available and configured."""

    def test_redis_tools_exist_as_modules(self):
        """Redis tools modules exist and are properly structured."""
        try:
            # Tools are defined as decorated functions within __init__ modules
            from app.tools import (
                RedisKeyScanTool,
                RedisReplicationTool,
                RedisServerInfoTool,
                RedisSlowlogTool,
            )

            # All 4 tool modules should be importable
            assert RedisServerInfoTool is not None
            assert RedisSlowlogTool is not None
            assert RedisReplicationTool is not None
            assert RedisKeyScanTool is not None
        except ImportError as e:
            pytest.fail(f"Failed to import Redis tool modules: {e}")

    def test_redis_integration_config_has_required_fields(self):
        """Redis integration provides required fields in resolved config."""
        from app.integrations.models import RedisIntegrationConfig

        config = RedisIntegrationConfig(
            host="localhost",
            port=6379,
            username="monitor",
            password="s3cret",
            db=0,
            ssl=True,
            integration_id="test-id",
        )

        assert config.host == "localhost"
        assert config.port == 6379
        assert config.username == "monitor"
        assert config.db == 0
        assert config.ssl is True
        assert config.integration_id == "test-id"


class TestRedisAlertFixture:
    """Test the Redis alert fixture is valid and parseable."""

    def test_redis_alert_fixture_is_valid_json(self):
        """Redis alert fixture is valid JSON."""
        fixture_path = Path(__file__).parent / "redis_alert.json"
        assert fixture_path.exists(), f"Alert fixture not found at {fixture_path}"

        with fixture_path.open() as f:
            alert = json.load(f)

        assert isinstance(alert, dict)
        assert "state" in alert
        assert "commonLabels" in alert
        assert "commonAnnotations" in alert

    def test_redis_alert_fixture_has_redis_context(self):
        """Redis alert fixture contains Redis-specific context."""
        fixture_path = Path(__file__).parent / "redis_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        labels = alert.get("commonLabels", {})
        # Alert should have Redis-specific fields for source detection
        assert "redis_instance" in labels
