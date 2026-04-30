"""Strict models for resolved effective integrations."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel


class IntegrationInstance(StrictConfigModel):
    """One named instance of a provider."""

    name: str = "default"
    tags: dict[str, str] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        text = str(value or "default").strip().lower()
        return text or "default"

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str] = {}
        for key, value_text in value.items():
            normalized_key = str(key).strip().lower()
            normalized_value = str(value_text).strip().lower()
            if (
                normalized_key
                and normalized_value
                and re.match(r"^[a-z][a-z0-9_-]*$", normalized_key)
            ):
                normalized[normalized_key] = normalized_value
        return normalized


class EffectiveIntegrationEntry(StrictConfigModel):
    """Resolved integration entry with source metadata."""

    source: str
    config: dict[str, Any]
    instances: list[dict[str, Any]] | None = None


class EffectiveIntegrations(StrictConfigModel):
    """Strict container for normalized effective integrations."""

    grafana: EffectiveIntegrationEntry | None = None
    datadog: EffectiveIntegrationEntry | None = None
    honeycomb: EffectiveIntegrationEntry | None = None
    coralogix: EffectiveIntegrationEntry | None = None
    aws: EffectiveIntegrationEntry | None = None
    slack: EffectiveIntegrationEntry | None = None
    tracer: EffectiveIntegrationEntry | None = None
    github: EffectiveIntegrationEntry | None = None
    sentry: EffectiveIntegrationEntry | None = None
    mongodb: EffectiveIntegrationEntry | None = None
    mongodb_atlas: EffectiveIntegrationEntry | None = None
    mariadb: EffectiveIntegrationEntry | None = None
    rabbitmq: EffectiveIntegrationEntry | None = None
    betterstack: EffectiveIntegrationEntry | None = None
    google_docs: EffectiveIntegrationEntry | None = None
    gitlab: EffectiveIntegrationEntry | None = None
    vercel: EffectiveIntegrationEntry | None = None
    jira: EffectiveIntegrationEntry | None = None
    opsgenie: EffectiveIntegrationEntry | None = None
    notion: EffectiveIntegrationEntry | None = None
    prefect: EffectiveIntegrationEntry | None = None
    posthog: EffectiveIntegrationEntry | None = None
    kafka: EffectiveIntegrationEntry | None = None
    clickhouse: EffectiveIntegrationEntry | None = None
    postgresql: EffectiveIntegrationEntry | None = None
    azure_sql: EffectiveIntegrationEntry | None = None
    bitbucket: EffectiveIntegrationEntry | None = None
    trello: EffectiveIntegrationEntry | None = None
    discord: EffectiveIntegrationEntry | None = None
    telegram: EffectiveIntegrationEntry | None = None
    openclaw: EffectiveIntegrationEntry | None = None
    mysql: EffectiveIntegrationEntry | None = None
    snowflake: EffectiveIntegrationEntry | None = None
    azure: EffectiveIntegrationEntry | None = None
    openobserve: EffectiveIntegrationEntry | None = None
    opensearch: EffectiveIntegrationEntry | None = None
    alertmanager: EffectiveIntegrationEntry | None = None
    splunk: EffectiveIntegrationEntry | None = None
    airflow: dict[str, Any] | None = None
    argocd: EffectiveIntegrationEntry | None = None
