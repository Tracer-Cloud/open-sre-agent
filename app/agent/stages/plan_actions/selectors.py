"""Alert-context selectors shared by investigation planning."""

from __future__ import annotations

from typing import Any

from app.agent.utils.alert_source import resolve_alert_source

# Maps alert_source values to integration source keys (tool `.source` field).
# An alert source can map to multiple integration sources.
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

# Generic fallback sources: useful, but never primary when incident-specific
# integrations match.
SECONDARY_SOURCES = {"knowledge", "openclaw", "google_docs"}

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


def primary_sources_for_alert(state: dict[str, Any]) -> tuple[str, ...]:
    """Return source keys that directly match the parsed alert source."""
    return ALERT_SOURCE_TO_TOOL_SOURCES.get(resolve_alert_source(state), ())


def relevant_sources_for_alert(
    state: dict[str, Any],
    candidate_sources: set[str],
) -> list[str]:
    """Select candidate sources relevant to the alert content."""
    candidates = sorted(source for source in candidate_sources if source not in SECONDARY_SOURCES)
    if not candidates:
        return []

    declared = declared_context_sources(state)
    if declared:
        from_declared = [source for source in candidates if source in declared]
        if from_declared:
            return from_declared

    text = collect_alert_text(state)
    if not text:
        return []

    matched: list[str] = []
    for source in candidates:
        keywords = {source, *SOURCE_ALIASES.get(source, ())}
        if any(keyword in text for keyword in keywords):
            matched.append(source)
    return matched


def declared_context_sources(state: dict[str, Any]) -> set[str]:
    """Return explicit context source annotations from the raw alert, if any."""
    raw = state.get("raw_alert")
    if not isinstance(raw, dict):
        return set()
    for block_key in ("commonAnnotations", "annotations", "commonLabels", "labels"):
        block = raw.get(block_key)
        if isinstance(block, dict):
            value = block.get("context_sources")
            if isinstance(value, str) and value.strip():
                return {item.strip().lower() for item in value.split(",") if item.strip()}
    return set()


def collect_alert_text(state: dict[str, Any]) -> str:
    """Collect searchable alert text for deterministic source/tool matching."""
    parts: list[str] = [
        str(state.get("alert_name") or ""),
        str(state.get("pipeline_name") or ""),
        str(state.get("message") or ""),
    ]
    raw = state.get("raw_alert")
    if isinstance(raw, dict):
        for key in ("alert_name", "title", "message", "text", "error_message", "kube_namespace"):
            value = raw.get(key)
            if isinstance(value, str):
                parts.append(value)
        for block_key in ("commonAnnotations", "annotations", "commonLabels", "labels"):
            block = raw.get(block_key)
            if isinstance(block, dict):
                parts.extend(str(v) for v in block.values() if isinstance(v, (str, int, float)))
    elif isinstance(raw, str):
        parts.append(raw)

    problem_md = state.get("problem_md")
    if isinstance(problem_md, str):
        parts.append(problem_md)

    return " ".join(part for part in parts if part).lower()


__all__ = [
    "ALERT_SOURCE_TO_TOOL_SOURCES",
    "SECONDARY_SOURCES",
    "SOURCE_ALIASES",
    "collect_alert_text",
    "declared_context_sources",
    "primary_sources_for_alert",
    "relevant_sources_for_alert",
]
