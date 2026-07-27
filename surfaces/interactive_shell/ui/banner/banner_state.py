"""Live system state helpers for the agent ready-state banner.

Queries integration health and alert-listener config without making
network calls — results are cached-once per banner render and used by
:func:`interactive_shell.ui.banner.banner._build_ambient_right_column`.
"""

from __future__ import annotations

from rich.text import Text

from platform.terminal.theme import BRAND, DIM, HIGHLIGHT, SECONDARY, WARNING

# Display-name overrides for known integration service slugs.
_SERVICE_DISPLAY_NAMES: dict[str, str] = {
    "grafana": "Grafana",
    "datadog": "Datadog",
    "honeycomb": "Honeycomb",
    "coralogix": "Coralogix",
    "aws": "AWS",
    "github": "GitHub",
    "sentry": "Sentry",
    "prometheus": "Prometheus",
    "loki": "Loki",
    "elasticsearch": "Elasticsearch",
    "bigquery": "BigQuery",
    "pagerduty": "PagerDuty",
    "slack": "Slack",
    "telegram": "Telegram",
    "signoz": "SigNoz",
    "jira": "Jira",
    "servicenow": "ServiceNow",
    "gitlab": "GitLab",
    "vercel": "Vercel",
    "mongodb": "MongoDB",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "redis": "Redis",
    "kafka": "Kafka",
    "rabbitmq": "RabbitMQ",
    "clickhouse": "ClickHouse",
    "mariadb": "MariaDB",
    "kubernetes": "Kubernetes",
    "betterstack": "Better Stack",
    "snowflake": "Snowflake",
    "newrelic": "New Relic",
    "opsgenie": "OpsGenie",
    "linear": "Linear",
    "supabase": "Supabase",
}


def integration_display_name(service: str) -> str:
    """Human-readable label for an integration service slug."""
    return _SERVICE_DISPLAY_NAMES.get(service, service.replace("_", " ").title())


def _load_integration_health() -> list[tuple[str, str]]:
    """Return ``(display_name, status)`` for each configured integration.

    ``status`` is ``"ok"`` or ``"incomplete"`` (e.g. a hosted MCP record saved
    without an API token). Offline and best-effort: never raises and never makes
    network calls, so the banner reflects health without slowing startup.
    """
    try:
        from integrations.catalog import (  # lazy — avoids circular deps
            configured_integration_health,
        )

        return [
            (integration_display_name(service), status)
            for service, status in configured_integration_health()
        ]
    except Exception:
        return []


def _is_alert_listener_active() -> bool:
    """Return True if the alert listener is enabled in config. Never raises."""
    try:
        from config.repl_config import ReplConfig

        return ReplConfig.load(apply_active_theme=False).alert_listener_enabled
    except Exception:
        return False


def _build_ambient_right_column(session: object = None) -> Text:
    """Right column for returning users: live integration status and alert listener state."""
    parts: list[Text] = []

    # Integrations — annotate by offline health so the banner never implies a
    # half-configured integration (e.g. a hosted MCP record with no API token)
    # is connected. A "⚠" + dim name marks an integration missing credentials.
    parts.append(Text("Integrations", style=f"bold {BRAND}"))
    entries = _load_integration_health()
    if entries:
        _MAX_SHOWN = 6
        shown = entries[:_MAX_SHOWN]
        overflow = len(entries) - len(shown)
        name_line = Text(overflow="fold")
        for idx, (name, status) in enumerate(shown):
            if idx:
                name_line.append("  ·  ", style=DIM)
            if status == "incomplete":
                name_line.append(f"{name} ⚠", style=DIM)
            else:
                name_line.append(name, style=SECONDARY)
        if overflow:
            name_line.append(f"  +{overflow}", style=DIM)
        parts.append(name_line)
        if any(status == "incomplete" for _name, status in entries):
            parts.append(Text("⚠ incomplete — run /integrations verify", style=WARNING))
    else:
        parts.append(Text("run /onboard to connect tools", style=DIM))

    parts.append(Text("───", style=DIM))

    # Alert listener
    parts.append(Text("Alert listener", style=f"bold {BRAND}"))
    if _is_alert_listener_active():
        listener_line = Text()
        listener_line.append("● ", style=f"bold {HIGHLIGHT}")
        listener_line.append("active", style=SECONDARY)
        parts.append(listener_line)
    else:
        parts.append(Text("○  not configured", style=DIM))

    # Session summary — only shown when /clear is used mid-session with history
    if session is not None:
        history: list[object] = getattr(session, "history", [])
        if history:
            parts.append(Text("───", style=DIM))
            parts.append(Text("This session", style=f"bold {BRAND}"))
            count = len(history)
            noun = "interaction" if count == 1 else "interactions"
            parts.append(Text(f"{count} {noun}", style=SECONDARY))

    return Text("\n").join(parts)


__all__ = [
    "_SERVICE_DISPLAY_NAMES",
    "integration_display_name",
    "_build_ambient_right_column",
    "_is_alert_listener_active",
    "_load_integration_health",
]
