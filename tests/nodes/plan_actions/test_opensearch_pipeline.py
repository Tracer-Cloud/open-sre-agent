"""End-to-end pipeline coverage for the OpenSearch integration.

These scenarios drive the deterministic plumbing layer from alert ingest to
tool params:
  alert -> resolve_integrations -> detect_sources -> tool extract_params

Each scenario uses a different auth mode (Basic Auth, API key, no auth) and a
different alert shape (Alertmanager-style, plain dict). The assertions verify
that credentials thread end-to-end through the runtime sources dict and into
the tool's extract_params output, which is the exact path that broke before
PR #1143.

This file lives under tests/nodes/plan_actions/ rather than tests/synthetic/
because it covers the deterministic detect_sources -> extract_params plumbing,
not LLM-driven scenarios with mocked HTTP or fixture backends. Adding a real
LLM-driven synthetic suite for OpenSearch is tracked as follow-up.
"""

from __future__ import annotations

from typing import Any

from app.nodes.plan_actions.detect_sources import detect_sources
from app.tools.ElasticsearchLogsTool import ElasticsearchLogsTool
from app.tools.OpenSearchAnalyticsTool import _opensearch_extract_params


def _basic_auth_alert() -> dict[str, Any]:
    """OpenSearch-routed alert pointing at an indexed service.

    No alert_source is set, which is the catch-all path for OpenSearch
    routing (matches alert_source in ("opensearch", "elasticsearch", "")).
    """
    return {
        "alert_name": "ServiceErrorRateHigh",
        "annotations": {
            "summary": "Error rate exceeded 5% threshold",
            "service_name": "checkout-api",
            "opensearch_index_pattern": "logs-checkout-*",
        },
        "alerts": [{"startsAt": "2025-11-01T10:30:00Z"}],
    }


def _api_key_alert() -> dict[str, Any]:
    """Plain alert without alert_source — exercises the OpenSearch fall-through."""
    return {
        "alert_name": "DatabaseConnectionPoolExhausted",
        "annotations": {
            "summary": "Connection pool at 100%",
        },
    }


def test_synthetic_basic_auth_threads_through_pipeline() -> None:
    """End-to-end: Basic-Auth catalog record -> sources dict -> tool params."""
    alert = _basic_auth_alert()
    integrations = {
        "opensearch": {
            "url": "https://opensearch.internal.example.com",
            "username": "opensre-readonly",
            "password": "secret-pass-123",
            "index_pattern": "logs-*",
            "integration_id": "opensearch-prod",
        }
    }

    sources = detect_sources(alert, {}, integrations)
    opensearch = sources["opensearch"]

    assert opensearch["url"] == "https://opensearch.internal.example.com"
    assert opensearch["username"] == "opensre-readonly"
    assert opensearch["password"] == "secret-pass-123"
    assert opensearch["api_key"] == ""
    assert opensearch["connection_verified"] is True

    # detect_sources aliases sources["opensearch"] under sources["elasticsearch"]
    # so ElasticsearchLogsTool (source="elasticsearch") is reachable from a
    # single opensearch wizard configuration. Verify the alias is the same
    # dict reference, not a copy.
    assert sources["elasticsearch"] is sources["opensearch"]

    logs_tool = ElasticsearchLogsTool()
    logs_params = logs_tool.extract_params(sources)
    assert logs_params["username"] == "opensre-readonly"
    assert logs_params["password"] == "secret-pass-123"

    analytics_params = _opensearch_extract_params(sources)
    assert analytics_params["username"] == "opensre-readonly"
    assert analytics_params["password"] == "secret-pass-123"


def test_synthetic_api_key_threads_through_pipeline() -> None:
    """End-to-end: API-key catalog record -> sources dict -> tool params.

    Verifies that adding Basic Auth support in PR #1143 did not break the
    pre-existing API-key path.
    """
    alert = _api_key_alert()
    integrations = {
        "opensearch": {
            "url": "https://my-deployment.es.us-east-1.aws.found.io",
            "api_key": "VnVhQ2ZHY0JDZGJrU...",
            "integration_id": "elastic-cloud-prod",
        }
    }

    sources = detect_sources(alert, {}, integrations)
    opensearch = sources["opensearch"]

    assert opensearch["url"] == "https://my-deployment.es.us-east-1.aws.found.io"
    assert opensearch["api_key"] == "VnVhQ2ZHY0JDZGJrU..."
    assert opensearch["username"] == ""
    assert opensearch["password"] == ""

    analytics_params = _opensearch_extract_params(sources)
    assert analytics_params["api_key"] == "VnVhQ2ZHY0JDZGJrU..."
    assert analytics_params["username"] == ""
    assert analytics_params["password"] == ""


def test_synthetic_no_auth_threads_through_pipeline() -> None:
    """End-to-end: URL-only catalog record -> sources dict -> tool params.

    Some self-hosted clusters run with the OpenSearch security plugin disabled
    on a trusted internal network. The classifier accepts URL-only records,
    and detect_sources must produce empty credential strings (not None) so
    downstream .strip() calls in the tools do not raise.
    """
    alert = _basic_auth_alert()
    integrations = {
        "opensearch": {
            "url": "http://opensearch.dev.internal:9200",
            "integration_id": "opensearch-dev",
        }
    }

    sources = detect_sources(alert, {}, integrations)
    opensearch = sources["opensearch"]

    assert opensearch["url"] == "http://opensearch.dev.internal:9200"
    assert opensearch["api_key"] == ""
    assert opensearch["username"] == ""
    assert opensearch["password"] == ""

    analytics_params = _opensearch_extract_params(sources)
    assert analytics_params["url"] == "http://opensearch.dev.internal:9200"
    assert analytics_params["api_key"] == ""
    assert analytics_params["username"] == ""
    assert analytics_params["password"] == ""


def test_synthetic_opensearch_coexists_with_other_integrations() -> None:
    """OpenSearch routing must not interfere with other configured integrations.

    Adding OpenSearch to the menu and the source-detection path could in
    principle have broken the routing of unrelated integrations. This scenario
    confirms that a multi-integration setup still routes each source correctly.
    """
    alert = _basic_auth_alert()
    integrations = {
        "opensearch": {
            "url": "https://opensearch.example.com",
            "username": "admin",
            "password": "secret",
        },
        "openobserve": {
            "base_url": "https://oo.example.com",
            "username": "oo-user",
            "password": "oo-pass",
        },
        "snowflake": {
            "account_identifier": "xy12345",
            "token": "sf-token",
        },
    }

    sources = detect_sources(alert, {}, integrations)

    assert sources["opensearch"]["username"] == "admin"
    assert sources["opensearch"]["password"] == "secret"
    assert sources["openobserve"]["username"] == "oo-user"
    assert sources["openobserve"]["password"] == "oo-pass"
    assert sources["snowflake"]["token"] == "sf-token"
