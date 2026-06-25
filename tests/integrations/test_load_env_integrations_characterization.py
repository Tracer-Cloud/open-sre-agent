"""Characterization test locking ``load_env_integrations`` output.

This is a behavior snapshot, not a behavior spec: it pins the exact records the
loader produces for a broad env matrix so the per-integration loader refactor
(issue #3043) can be proven byte-identical. The golden file is generated from
the pre-refactor implementation; if loader behavior intentionally changes later,
regenerate it with ``scripts`` below and review the diff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.integrations._catalog_impl import load_env_integrations

_GOLDEN = Path(__file__).parent / "_data" / "load_env_integrations_golden.json"

# A broad env matrix that exercises as many env-loader branches as possible:
# single-instance credentials, the AWS role path, MCP-mode integrations, the
# shared-Twilio WhatsApp + SMS split, and helper-backed loaders. Integrations
# whose validation rejects these placeholder values simply stay absent — the
# snapshot still pins that "skip" outcome.
_ENV: dict[str, str] = {
    "GRAFANA_INSTANCE_URL": "https://grafana.example.com",
    "GRAFANA_READ_TOKEN": "glsa_token",
    "DD_API_KEY": "dd-api-key",
    "DD_APP_KEY": "dd-app-key",
    "DD_SITE": "datadoghq.eu",
    "GROUNDCOVER_API_KEY": "gc-key",
    "GROUNDCOVER_MCP_URL": "https://mcp.groundcover.com",
    "GROUNDCOVER_TENANT_UUID": "tenant-uuid",
    "GROUNDCOVER_BACKEND_ID": "backend-id",
    "HONEYCOMB_API_KEY": "hc-key",
    "HONEYCOMB_DATASET": "dataset",
    "HONEYCOMB_API_URL": "https://api.honeycomb.io",
    "CORALOGIX_API_KEY": "cx-key",
    "CORALOGIX_API_URL": "https://api.coralogix.com",
    "CORALOGIX_APPLICATION_NAME": "app",
    "CORALOGIX_SUBSYSTEM_NAME": "sub",
    "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/test",
    "AWS_EXTERNAL_ID": "ext-id",
    "AWS_REGION": "us-west-2",
    "GITHUB_MCP_URL": "https://api.githubcopilot.com/mcp/",
    "GITHUB_MCP_AUTH_TOKEN": "gh-token",
    "SENTRY_ORG_SLUG": "org",
    "SENTRY_AUTH_TOKEN": "sentry-token",
    "SENTRY_URL": "https://sentry.io",
    "SENTRY_PROJECT_SLUG": "proj",
    "GITLAB_ACCESS_TOKEN": "gl-token",
    "GITLAB_BASE_URL": "https://gitlab.com",
    "MONGODB_CONNECTION_STRING": "mongodb://localhost:27017",
    "MONGODB_DATABASE": "appdb",
    "POSTGRESQL_HOST": "localhost",
    "POSTGRESQL_DATABASE": "appdb",
    "POSTGRESQL_PORT": "5432",
    "POSTGRESQL_USERNAME": "postgres",
    "POSTGRESQL_PASSWORD": "pw",
    "ARGOCD_BASE_URL": "https://argocd.example.com",
    "ARGOCD_AUTH_TOKEN": "argocd-token",
    "OSRE_HELM_INTEGRATION": "true",
    "HELM_PATH": "helm",
    "VERCEL_API_TOKEN": "vercel-token",
    "VERCEL_TEAM_ID": "team",
    "OPSGENIE_API_KEY": "og-key",
    "OPSGENIE_REGION": "us",
    "PAGERDUTY_API_KEY": "pd-key",
    "INCIDENT_IO_API_KEY": "io-key",
    "JIRA_BASE_URL": "https://x.atlassian.net",
    "JIRA_EMAIL": "a@b.com",
    "JIRA_API_TOKEN": "jira-token",
    "JIRA_PROJECT_KEY": "PK",
    "DISCORD_BOT_TOKEN": "discord-token",
    "DISCORD_APPLICATION_ID": "app-id",
    "DISCORD_PUBLIC_KEY": "a" * 64,
    "TELEGRAM_BOT_TOKEN": "telegram-token",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "587",
    "SMTP_USERNAME": "user",
    "SMTP_PASSWORD": "pw",
    "SMTP_FROM_ADDRESS": "a@b.com",
    "TWILIO_ACCOUNT_SID": "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "TWILIO_AUTH_TOKEN": "twilio-token",
    "TWILIO_WHATSAPP_FROM": "+10000000001",
    "TWILIO_SMS_FROM": "+10000000002",
    "MONGODB_ATLAS_PUBLIC_KEY": "atlas-pub",
    "MONGODB_ATLAS_PRIVATE_KEY": "atlas-priv",
    "MONGODB_ATLAS_PROJECT_ID": "atlas-project",
    "OPENCLAW_MCP_URL": "https://openclaw.example.com",
    "OPENCLAW_MCP_AUTH_TOKEN": "openclaw-token",
    "POSTHOG_MCP_AUTH_TOKEN": "posthog-token",
    "SENTRY_MCP_AUTH_TOKEN": "sentry-mcp-token",
    "MARIADB_HOST": "localhost",
    "MARIADB_DATABASE": "appdb",
    "MARIADB_USERNAME": "user",
    "MARIADB_PASSWORD": "pw",
    "DAGSTER_ENDPOINT": "https://dagster.example.com",
    "DAGSTER_API_TOKEN": "dagster-token",
    "RABBITMQ_HOST": "localhost",
    "RABBITMQ_USERNAME": "guest",
    "RABBITMQ_PASSWORD": "guest",
    "BETTERSTACK_QUERY_ENDPOINT": "https://bs.example.com",
    "BETTERSTACK_USERNAME": "user",
    "BETTERSTACK_PASSWORD": "pw",
    "MYSQL_HOST": "localhost",
    "MYSQL_DATABASE": "appdb",
    "MYSQL_USERNAME": "root",
    "MYSQL_PASSWORD": "pw",
    "AZURE_SQL_SERVER": "server",
    "AZURE_SQL_DATABASE": "appdb",
    "AZURE_SQL_USERNAME": "user",
    "AZURE_SQL_PASSWORD": "pw",
    "BITBUCKET_WORKSPACE": "workspace",
    "BITBUCKET_USERNAME": "user",
    "BITBUCKET_APP_PASSWORD": "pw",
    "SNOWFLAKE_ACCOUNT": "acct",
    "SNOWFLAKE_TOKEN": "snowflake-token",
    "SNOWFLAKE_USER": "user",
    "AZURE_LOG_ANALYTICS_WORKSPACE_ID": "workspace-id",
    "AZURE_LOG_ANALYTICS_TOKEN": "azure-token",
    "OPENOBSERVE_URL": "https://oo.example.com",
    "OPENOBSERVE_TOKEN": "oo-token",
    "OPENSEARCH_URL": "https://os.example.com",
    "OPENSEARCH_USERNAME": "user",
    "OPENSEARCH_PASSWORD": "pw",
    "ALERTMANAGER_URL": "https://am.example.com",
    "VICTORIA_LOGS_URL": "https://vl.example.com",
    "SPLUNK_URL": "https://splunk.example.com",
    "SPLUNK_TOKEN": "splunk-token",
    "SUPABASE_URL": "https://x.supabase.co",
    "SUPABASE_SERVICE_KEY": "supabase-key",
    "TEMPORAL_API_URL": "https://temporal.example.com",
    "TEMPORAL_NAMESPACE": "default",
    "TEMPORAL_API_KEY": "temporal-token",
}


def _capture(env: dict[str, str]) -> list[dict[str, Any]]:
    """Run ``load_env_integrations`` under exactly ``env`` in loader emission order."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        result = load_env_integrations()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    # Return records in the loader's own emission order (no sorting) so the
    # golden comparison pins both content AND order — a registry reshuffle now
    # fails the snapshot instead of slipping through.
    return result


def test_load_env_integrations_matches_golden() -> None:
    if not _GOLDEN.exists():  # pragma: no cover - generation path
        pytest.skip(f"golden snapshot missing: {_GOLDEN}")
    # Round-trip through JSON so tuple-valued config fields (e.g. github
    # ``toolsets``) compare equal to their list form in the golden file.
    captured = json.loads(json.dumps(_capture(_ENV)))
    expected = json.loads(_GOLDEN.read_text())
    assert captured == expected


def test_loader_record_emission_order_matches_golden() -> None:
    if not _GOLDEN.exists():  # pragma: no cover - generation path
        pytest.skip(f"golden snapshot missing: {_GOLDEN}")
    captured_services = [r["service"] for r in _capture(_ENV)]
    golden_services = [r["service"] for r in json.loads(_GOLDEN.read_text())]
    # Full-sequence equality catches a registry reshuffle at any position, not
    # just the first record.
    assert captured_services == golden_services
    # Structural anchors independent of the golden: grafana leads, and the
    # shared Twilio loader emits the WhatsApp record immediately before the SMS
    # (twilio) record.
    assert captured_services[0] == "grafana"
    whatsapp_index = captured_services.index("whatsapp")
    assert captured_services[whatsapp_index + 1] == "twilio"
