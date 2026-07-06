"""Alert source resolution and tool-source routing helpers.

Naming convention:

- ``alert_source`` — vendor/format key on the incoming alert payload (e.g.
  ``"grafana"``, ``"eks"``). Keys ``ALERT_SOURCE_ROUTING``.
- ``tool source`` — integration key matching ``tool.source`` (e.g.
  ``"grafana"``, ``"ec2"``, ``"cloudtrail"``). Values in routing tuples.

Each ``AlertSourceRouting`` entry carries two tool-source lists:

- ``relevance_tool_sources`` — broad prioritization during tool planning.
- ``seed_tool_sources`` — narrower subset auto-invoked before the LLM loop.
  Expensive or context-dependent tools stay out of seeding.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from core.domain.alerts.fields import iter_alert_blocks


@dataclass(frozen=True)
class AlertSourceRouting:
    relevance_tool_sources: tuple[str, ...]
    seed_tool_sources: tuple[str, ...]


def _routing(
    relevance_tool_sources: tuple[str, ...],
    seed_tool_sources: tuple[str, ...],
) -> AlertSourceRouting:
    return AlertSourceRouting(
        relevance_tool_sources=relevance_tool_sources,
        seed_tool_sources=seed_tool_sources,
    )


# Single registry — relevance and seed lists are intentionally different per entry.
ALERT_SOURCE_ROUTING: dict[str, AlertSourceRouting] = {
    "grafana": _routing(("grafana",), ("grafana",)),
    "datadog": _routing(("datadog",), ("datadog",)),
    # ec2/rds/cloudtrail stay relevance-only — context-dependent pre-LLM seeding.
    "cloudwatch": _routing(("cloudwatch", "ec2", "rds", "cloudtrail"), ("cloudwatch",)),
    # ec2/cloudtrail stay relevance-only — seed only the cluster integration.
    "eks": _routing(("eks", "ec2", "cloudtrail"), ("eks",)),
    # eks/cloudtrail stay relevance-only — seed grafana + cloudwatch dashboards/logs.
    "alertmanager": _routing(
        ("eks", "cloudwatch", "grafana", "cloudtrail"),
        ("grafana", "cloudwatch"),
    ),
    "sentry": _routing(("sentry",), ("sentry",)),
    "honeycomb": _routing(("honeycomb",), ("honeycomb",)),
    "coralogix": _routing(("coralogix",), ("coralogix",)),
    # tracer_web stays relevance-only — secondary web context, not pre-LLM seed.
    "airflow": _routing(("airflow", "tracer_web"), ("airflow",)),
    "hermes": _routing(("hermes",), ("hermes",)),
    "kafka": _routing(("kafka",), ("kafka",)),
    "postgresql": _routing(("postgresql",), ("postgresql",)),
    "mysql": _routing(("mysql",), ("mysql",)),
    "mariadb": _routing(("mariadb",), ("mariadb",)),
    "mongodb": _routing(("mongodb", "mongodb_atlas"), ("mongodb", "mongodb_atlas")),
    "redis": _routing(("redis",), ("redis",)),
    "snowflake": _routing(("snowflake",), ("snowflake",)),
    "clickhouse": _routing(("clickhouse",), ("clickhouse",)),
    "dagster": _routing(("dagster",), ("dagster",)),
    "rabbitmq": _routing(("rabbitmq",), ("rabbitmq",)),
    "supabase": _routing(("supabase",), ("supabase",)),
    "opensearch": _routing(("opensearch",), ("opensearch",)),
    "openobserve": _routing(("openobserve",), ("openobserve",)),
    "betterstack": _routing(("betterstack",), ("betterstack",)),
    "azure": _routing(("azure", "azure_sql"), ("azure", "azure_sql")),
    "github": _routing(("github",), ("github",)),
    "gitlab": _routing(("gitlab",), ("gitlab",)),
    "bitbucket": _routing(("bitbucket",), ("bitbucket",)),
    "argocd": _routing(("eks",), ("eks",)),
    "splunk": _routing(("splunk",), ("splunk",)),
    "signoz": _routing(("signoz",), ("signoz",)),
    "jenkins": _routing(("jenkins",), ("jenkins",)),
    "tempo": _routing(("tempo",), ("tempo",)),
    "temporal": _routing(("temporal",), ("temporal",)),
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
    "temporal": ("temporal", "temporal workflow", "task queue"),
}


def routing_for_alert_source(alert_source: str) -> AlertSourceRouting | None:
    """Return routing config for a resolved alert vendor key, if known."""
    return ALERT_SOURCE_ROUTING.get(alert_source.strip().lower())


def primary_sources_for_alert(state: dict[str, Any]) -> tuple[str, ...]:
    """Return the routing entry's ``relevance_tool_sources`` for this alert.

    Used for broad alert-driven tool prioritization; callers surface these
    as ``primary_sources`` in plan audits and prompts.
    """
    routing = routing_for_alert_source(resolve_alert_source(state))
    return routing.relevance_tool_sources if routing is not None else ()


def seed_tool_sources_for_alert(state: dict[str, Any]) -> tuple[str, ...]:
    """Return tool sources auto-called before the investigation LLM loop."""
    routing = routing_for_alert_source(resolve_alert_source(state))
    return routing.seed_tool_sources if routing is not None else ()


def declared_context_sources(state: dict[str, Any]) -> set[str]:
    """Return explicit context source annotations from the raw alert, if any."""
    raw = state.get("raw_alert")
    if not isinstance(raw, dict):
        return set()
    for block in iter_alert_blocks(raw):
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
        for block in iter_alert_blocks(raw):
            parts.extend(str(v) for v in block.values() if isinstance(v, (str, int, float)))
    elif isinstance(raw, str):
        parts.append(raw)

    problem_md = state.get("problem_md")
    if isinstance(problem_md, str):
        parts.append(problem_md)

    return " ".join(part for part in parts if part).lower()


def relevant_sources_for_alert(
    state: dict[str, Any],
    candidate_sources: Iterable[str],
) -> list[str]:
    """Select candidate sources relevant to the alert content."""
    candidates = sorted(
        source for source in candidate_sources if source not in SECONDARY_TOOL_SOURCES
    )
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


def resolve_alert_source(state: dict[str, Any]) -> str:
    """Return the alert vendor key used to look up tool-source routing.

    Grafana managed alerts reuse the Alertmanager webhook schema, so
    ``alert_source`` is often missing from the payload — we sniff
    ``grafana_folder`` / ``datasource_uid`` labels and ``externalURL`` below.
    """
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


__all__ = [
    "ALERT_SOURCE_ROUTING",
    "AlertSourceRouting",
    "SECONDARY_TOOL_SOURCES",
    "SOURCE_ALIASES",
    "collect_alert_text",
    "declared_context_sources",
    "primary_sources_for_alert",
    "relevant_sources_for_alert",
    "resolve_alert_source",
    "routing_for_alert_source",
    "seed_tool_sources_for_alert",
]
