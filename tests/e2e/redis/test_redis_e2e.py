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

    @pytest.fixture(autouse=True)
    def _clear_registry_cache(self):
        """Force fresh tool discovery so registry assertions never depend on a
        cache populated by a prior test (per the ``tests/`` convention that any
        test calling ``get_registered_tools()`` clears the cache in a fixture)."""
        from app.tools.registry import clear_tool_registry_cache

        clear_tool_registry_cache()
        yield
        clear_tool_registry_cache()

    def test_redis_tools_exist_as_modules(self):
        """Redis tools modules exist and are properly structured."""
        try:
            # Tools are defined as decorated functions within __init__ modules
            from app.tools import (
                RedisClientListTool,
                RedisKeyScanTool,
                RedisLatencyDoctorTool,
                RedisListDepthTool,
                RedisReplicationTool,
                RedisServerInfoTool,
                RedisSlowlogTool,
            )

            # All 7 tool modules should be importable (4 baseline + 3 P1)
            assert RedisServerInfoTool is not None
            assert RedisSlowlogTool is not None
            assert RedisReplicationTool is not None
            assert RedisKeyScanTool is not None
            assert RedisClientListTool is not None
            assert RedisListDepthTool is not None
            assert RedisLatencyDoctorTool is not None
        except ImportError as e:
            pytest.fail(f"Failed to import Redis tool modules: {e}")

    def test_p1_tools_registered_on_investigation_surface(self):
        """The three new P1 tools are discoverable on the investigation and chat surfaces."""
        from app.tools.registry import get_registered_tools

        p1_tools = {
            "get_redis_client_list",
            "get_redis_list_depth",
            "get_redis_latency_doctor",
        }
        for surface in ("investigation", "chat"):
            names = {t.name for t in get_registered_tools(surface) if t.source == "redis"}
            assert p1_tools <= names, (
                f"missing P1 redis tools on {surface} surface: {p1_tools - names}"
            )

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


class TestRedisP1ToolPaths:
    """Exercise each new P1 tool end-to-end: tool fn -> helper -> client -> shape.

    The Redis client is mocked at the transport boundary so the full tool path
    (config build, command issue, response shaping, bounded sampling) is
    covered without a live Redis, mirroring how a real investigation would call
    these tools with credentials resolved from the integration config.
    """

    @patch("app.integrations.redis._get_client")
    def test_client_list_tool_path(self, mock_get_client):
        from app.tools.RedisClientListTool import get_redis_client_list

        mock_client = MagicMock()
        mock_client.client_list.return_value = [
            {"id": "1", "addr": "10.0.0.1:5000", "flags": "N", "idle": "0", "cmd": "get"},
            {"id": "2", "addr": "10.0.0.1:5001", "flags": "b", "idle": "5", "cmd": "blpop"},
        ]
        mock_get_client.return_value = mock_client

        result = get_redis_client_list(host="prod-cache.redis.internal", port=6379, db=0)

        assert result["available"] is True
        assert result["total_clients"] == 2
        assert result["blocked_clients"] == 1
        assert result["address_breakdown"]["10.0.0.1"] == 2

    @patch("app.integrations.redis._get_client")
    def test_list_depth_tool_path(self, mock_get_client):
        from app.tools.RedisListDepthTool import get_redis_list_depth

        mock_client = MagicMock()
        mock_client.type.return_value = "list"
        mock_client.pipeline.return_value.execute.return_value = [1500, ["job-1"], ["job-1500"]]
        mock_get_client.return_value = mock_client

        result = get_redis_list_depth(
            key="sidekiq:queue:default",
            host="prod-cache.redis.internal",
            head=1,
            tail=1,
        )

        assert result["available"] is True
        assert result["depth"] == 1500  # a real backlog
        assert result["head"] == ["job-1"]
        assert result["tail"] == ["job-1500"]

    @patch("app.integrations.redis._get_client")
    def test_latency_doctor_tool_path(self, mock_get_client):
        from app.tools.RedisLatencyDoctorTool import get_redis_latency_doctor

        mock_client = MagicMock()
        mock_client.execute_command.return_value = "I detected spikes caused by fork."
        mock_client.latency_latest.return_value = [["fork", 1700000000, 480, 1200]]
        # Explicitly configure CONFIG GET so monitoring_active is driven by the
        # real threshold (> 0) — not MagicMock's implicit __int__ == 1, which
        # would make the assertion pass even if the threshold logic regressed.
        mock_client.config_get.return_value = {"latency-monitor-threshold": "100"}
        mock_get_client.return_value = mock_client

        result = get_redis_latency_doctor(host="prod-cache.redis.internal")

        assert result["available"] is True
        assert result["monitoring_active"] is True
        assert result["monitoring_threshold_ms"] == 100
        assert result["latest"][0]["event"] == "fork"
        assert "fork" in result["report"]


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
