"""Shared constants for the OpenSRE CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    MANAGED_INTEGRATION_SERVICES: tuple[str, ...]
    SETUP_SERVICES: tuple[str, ...]
    VERIFY_SERVICES: tuple[str, ...]

# MANAGED_INTEGRATION_SERVICES, SETUP_SERVICES and VERIFY_SERVICES are PEP 562
# lazy module attributes resolved by `__getattr__` below; ruff's F822 check
# can't see them.
__all__ = (
    "MANAGED_INTEGRATION_SERVICES",
    "SAMPLE_ALERT_OPTIONS",
    "SETUP_SERVICES",
    "VERIFY_SERVICES",
)

# Template names for these options are the shared ALERT_TEMPLATE_CHOICES in
# config/constants/investigation.py; the labels here are CLI-only.
SAMPLE_ALERT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("generic", "Generic - High error rate in payments ETL"),
    ("datadog", "Datadog - payments-etl error rate high"),
    ("grafana", "Grafana - Pipeline failure rate high"),
    ("honeycomb", "Honeycomb - checkout-api latency regression"),
    ("coralogix", "Coralogix - payments worker errors"),
    ("new_relic", "New Relic - checkout-latency threshold breach"),
    ("splunk", "Splunk - payments service error spike"),
)


def __getattr__(name: str) -> tuple[str, ...]:
    # SETUP_SERVICES, VERIFY_SERVICES and MANAGED_INTEGRATION_SERVICES are
    # sourced from the runtime integration registry so the CLI's positional-arg
    # `click.Choice` validators stay in sync with what cmd_setup / cmd_verify
    # can actually dispatch. Eagerly importing `integrations.registry` here
    # creates a circular import (registry -> verifiers -> integrations.github.mcp ->
    # cli.*). Deferring the import until first access allows the CLI to finish
    # bootstrapping before the integration registry is loaded.
    if name == "SETUP_SERVICES":
        from integrations.registry import SUPPORTED_SETUP_SERVICES

        return tuple(SUPPORTED_SETUP_SERVICES)
    if name == "VERIFY_SERVICES":
        from integrations.registry import SUPPORTED_VERIFY_SERVICES

        return tuple(SUPPORTED_VERIFY_SERVICES)
    if name == "MANAGED_INTEGRATION_SERVICES":
        from integrations.registry import (
            SUPPORTED_SETUP_SERVICES,
            SUPPORTED_VERIFY_SERVICES,
        )

        return tuple(sorted(set(SUPPORTED_SETUP_SERVICES) | set(SUPPORTED_VERIFY_SERVICES)))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
