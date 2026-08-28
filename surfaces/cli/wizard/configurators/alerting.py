"""Configurator handlers for alerting and on-call integrations."""

from __future__ import annotations

from config.env_file import sync_env_values
from infrastructure.terminal.theme import SECONDARY
from integrations.alertmanager import ALERTMANAGER_SETUP
from integrations.betterstack.setup import BETTERSTACK_SETUP
from integrations.incident_io import INCIDENT_IO_SETUP
from integrations.pagerduty import PAGERDUTY_SETUP
from integrations.store import upsert_integration
from surfaces.cli.wizard.components import (
    console,
    integration_defaults,
    prompt_value,
    string_value,
)
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec
from surfaces.cli.wizard.integration_health import (
    validate_opsgenie_integration,
)
from surfaces.cli.wizard.summaries import render_integration_result


def _configure_betterstack() -> tuple[str, str]:
    return configure_from_spec(BETTERSTACK_SETUP, title="Better Stack")


def _configure_alertmanager() -> tuple[str, str]:
    return configure_from_spec(ALERTMANAGER_SETUP, title="Alertmanager")


def _configure_opsgenie() -> tuple[str, str]:
    _, credentials = integration_defaults("opsgenie")
    while True:
        api_key = prompt_value(
            "OpsGenie API key (Settings > API key management)",
            default=string_value(credentials.get("api_key")),
            secret=True,
        )
        region = prompt_value(
            "OpsGenie region (us or eu)",
            default=string_value(credentials.get("region"), "us"),
        )
        with console.status("Validating OpsGenie integration...", spinner="dots"):
            result = validate_opsgenie_integration(api_key=api_key, region=region)
        render_integration_result("OpsGenie", result)
        if result.ok:
            upsert_integration(
                "opsgenie",
                {"credentials": {"api_key": api_key, "region": region}},
            )
            env_path = sync_env_values({})
            return "OpsGenie", str(env_path)
        console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_pagerduty() -> tuple[str, str]:
    return configure_from_spec(PAGERDUTY_SETUP, title="PagerDuty")


def _configure_incident_io() -> tuple[str, str]:
    return configure_from_spec(INCIDENT_IO_SETUP, title="incident.io")
