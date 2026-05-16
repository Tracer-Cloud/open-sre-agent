"""Shared constants for the OpenSRE CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    MANAGED_INTEGRATION_SERVICES: tuple[str, ...]
    VERIFY_SERVICES: tuple[str, ...]

# MANAGED_INTEGRATION_SERVICES and VERIFY_SERVICES are PEP 562 lazy module
# attributes resolved by `__getattr__` below; ruff's F822 check can't see them.
__all__ = (
    "ALERT_TEMPLATE_CHOICES",
    "MANAGED_INTEGRATION_SERVICES",
    "SAMPLE_ALERT_OPTIONS",
    "SETUP_SERVICES",
    "VERIFY_SERVICES",
)

ALERT_TEMPLATE_CHOICES: tuple[str, ...] = (
    "generic",
    "datadog",
    "grafana",
    "honeycomb",
    "coralogix",
)

SAMPLE_ALERT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("generic", "Generic - High error rate in payments ETL"),
    ("datadog", "Datadog - payments-etl error rate high"),
    ("grafana", "Grafana - Pipeline failure rate high"),
    ("honeycomb", "Honeycomb - checkout-api latency regression"),
    ("coralogix", "Coralogix - payments worker errors"),
)

SETUP_SERVICES: tuple[str, ...] = (
    "alertmanager",
    "aws",
    "betterstack",
    "coralogix",
    "datadog",
    "discord",
    "grafana",
    "github",
    "gitlab",
    "honeycomb",
    "incident_io",
    "mariadb",
    "mongodb",
    "mongodb_atlas",
    "mysql",
    "openclaw",
    "opensearch",
    "postgresql",
    "rabbitmq",
    "rds",
    "sentry",
    "slack",
    "tracer",
    "vercel",
)


def __getattr__(name: str) -> tuple[str, ...]:
    # VERIFY_SERVICES and MANAGED_INTEGRATION_SERVICES are sourced from the
    # runtime integration registry so the CLI's positional-arg `click.Choice`
    # validators stay in sync with what cmd_verify can actually dispatch.
    # Eagerly importing `app.integrations.registry` here creates a circular
    # import (registry -> _verification_adapters -> github_mcp -> app.cli.*).
    # Deferring to first access lets `app.cli` finish bootstrapping. See #1973.
    if name == "VERIFY_SERVICES":
        from app.integrations.registry import SUPPORTED_VERIFY_SERVICES

        return SUPPORTED_VERIFY_SERVICES
    if name == "MANAGED_INTEGRATION_SERVICES":
        from app.integrations.registry import SUPPORTED_VERIFY_SERVICES

        return tuple(sorted(set(SETUP_SERVICES) | set(SUPPORTED_VERIFY_SERVICES)))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
