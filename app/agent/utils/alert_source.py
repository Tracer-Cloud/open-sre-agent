"""Alert source resolution and tool-source routing helpers."""

from __future__ import annotations

from typing import Any

# Maps alert_source values to integration source keys (tool `.source` field).
# Used for broad prioritization/relevance, not automatic pre-seeding.
ALERT_SOURCE_TO_TOOL_SOURCES: dict[str, tuple[str, ...]] = {
    "grafana": ("grafana",),
    "datadog": ("datadog",),
    "cloudwatch": ("cloudwatch", "ec2", "rds", "cloudtrail"),
    "eks": ("eks", "ec2", "cloudtrail"),
    "alertmanager": ("eks", "cloudwatch", "grafana", "cloudtrail"),
    "sentry": ("sentry",),
    "honeycomb": ("honeycomb",),
    "coralogix": ("coralogix",),
    "airflow": ("airflow", "tracer_web"),
    "hermes": ("hermes",),
    "kafka": ("kafka",),
    "postgresql": ("postgresql",),
    "mysql": ("mysql",),
    "mariadb": ("mariadb",),
    "mongodb": ("mongodb", "mongodb_atlas"),
    "redis": ("redis",),
    "snowflake": ("snowflake",),
    "clickhouse": ("clickhouse",),
    "dagster": ("dagster",),
    "rabbitmq": ("rabbitmq",),
    "supabase": ("supabase",),
    "opensearch": ("opensearch",),
    "openobserve": ("openobserve",),
    "betterstack": ("betterstack",),
    "azure": ("azure", "azure_sql"),
    "github": ("github",),
    "gitlab": ("gitlab",),
    "bitbucket": ("bitbucket",),
    "argocd": ("eks",),
    "splunk": ("splunk",),
    "signoz": ("signoz",),
    "jenkins": ("jenkins",),
    "tempo": ("tempo",),
}

# Auto-called before the LLM loop starts. Keep this narrower than
# ALERT_SOURCE_TO_TOOL_SOURCES for expensive or context-dependent tools.
ALERT_SOURCE_TO_SEED_TOOL_SOURCES: dict[str, tuple[str, ...]] = {
    "grafana": ("grafana",),
    "datadog": ("datadog",),
    "cloudwatch": ("cloudwatch",),
    "eks": ("eks",),
    "alertmanager": ("grafana", "cloudwatch"),
    "sentry": ("sentry",),
    "honeycomb": ("honeycomb",),
    "coralogix": ("coralogix",),
    "airflow": ("airflow",),
    "hermes": ("hermes",),
    "kafka": ("kafka",),
    "postgresql": ("postgresql",),
    "mysql": ("mysql",),
    "mariadb": ("mariadb",),
    "mongodb": ("mongodb", "mongodb_atlas"),
    "redis": ("redis",),
    "snowflake": ("snowflake",),
    "clickhouse": ("clickhouse",),
    "dagster": ("dagster",),
    "rabbitmq": ("rabbitmq",),
    "supabase": ("supabase",),
    "opensearch": ("opensearch",),
    "openobserve": ("openobserve",),
    "betterstack": ("betterstack",),
    "azure": ("azure", "azure_sql"),
    "splunk": ("splunk",),
    "signoz": ("signoz",),
    "jenkins": ("jenkins",),
    "tempo": ("tempo",),
}

# Generic fallback sources: useful, but never primary when incident-specific
# integrations match.
SECONDARY_TOOL_SOURCES = frozenset({"knowledge", "openclaw", "google_docs"})

DB_KEYWORDS: tuple[str, ...] = ("database", "db connection", "connection pool")

SOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    "datadog": ("datadog", "datadoghq", "dd monitor"),
    "sentry": ("sentry", "exception", "stack trace", "stacktrace", "error tracking"),
    "vercel": ("vercel", "deploy", "deployment", "build failed"),
    "github": ("github", "commit", "pull request", "merge"),
    "gitlab": ("gitlab", "merge request"),
    "grafana": ("grafana", "loki", "mimir", "prometheus"),
    "honeycomb": ("honeycomb", "span", "trace latency"),
    "coralogix": ("coralogix",),
    "splunk": ("splunk",),
    "cloudwatch": ("cloudwatch", "lambda", "log group"),
    "eks": ("eks", "kubernetes", "k8s", "kubectl", "pod"),
    "ec2": ("ec2", "instance"),
    "rds": ("rds", "aurora", *DB_KEYWORDS),
    "postgresql": ("postgres", "postgresql", "psql", *DB_KEYWORDS),
    "mysql": ("mysql", *DB_KEYWORDS),
    "mariadb": ("mariadb", *DB_KEYWORDS),
    "mongodb": ("mongodb", "mongo", *DB_KEYWORDS),
    "redis": ("redis", "cache"),
    "snowflake": ("snowflake",),
    "clickhouse": ("clickhouse",),
    "dagster": ("dagster",),
    "airflow": ("airflow", "dag"),
    "kafka": ("kafka",),
    "rabbitmq": ("rabbitmq", "amqp"),
    "supabase": ("supabase",),
    "opensearch": ("opensearch", "elasticsearch"),
    "openobserve": ("openobserve",),
    "betterstack": ("betterstack", "better stack"),
    "azure": ("azure",),
    "signoz": ("signoz",),
    "jenkins": ("jenkins",),
    "tempo": ("tempo",),
}


def resolve_alert_source(state: dict[str, Any]) -> str:
    source = str(state.get("alert_source") or "").lower().strip()
    if source:
        return source
    raw = state.get("raw_alert")
    if isinstance(raw, dict):
        source = str(raw.get("alert_source") or "").lower().strip()
        if source:
            return source
        labels = raw.get("commonLabels") or raw.get("labels") or {}
        if isinstance(labels, dict) and (
            labels.get("grafana_folder") or labels.get("datasource_uid")
        ):
            return "grafana"
        ext_url = raw.get("externalURL", "")
        if isinstance(ext_url, str) and "grafana" in ext_url.lower():
            return "grafana"
    return ""
