"""Configurator handlers for alerting and on-call integrations."""

from __future__ import annotations

from config.env_file import sync_env_secret, sync_env_values
from integrations.betterstack.setup import BETTERSTACK_SETUP
from integrations.store import upsert_integration
from platform.terminal.theme import ERROR, SECONDARY
from surfaces.cli.wizard._ui import (
    Choice,
    _choose,
    _console,
    _integration_defaults,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec
from surfaces.cli.wizard.integration_health import (
    validate_alertmanager_integration,
    validate_incident_io_integration,
    validate_opsgenie_integration,
    validate_pagerduty_integration,
)


def _configure_betterstack() -> tuple[str, str]:
    return configure_from_spec(BETTERSTACK_SETUP, title="Better Stack")


def _configure_alertmanager() -> tuple[str, str]:
    _, credentials = _integration_defaults("alertmanager")
    while True:
        base_url = _prompt_value(
            "Alertmanager URL (e.g. http://alertmanager:9093)",
            default=_string_value(credentials.get("base_url")),
        )
        if not base_url:
            _console.print(f"[{ERROR}]Alertmanager URL is required.[/]")
            continue
        auth_choice = _choose(
            "Authentication method",
            [
                Choice(value="none", label="None (unauthenticated / internal network)"),
                Choice(value="bearer", label="Bearer token (reverse proxy auth)"),
                Choice(value="basic", label="Basic auth (username + password)"),
            ],
            default="none",
        )
        bearer_token = ""
        username = ""
        password = ""
        if auth_choice == "bearer":
            bearer_token = _prompt_value("Bearer token", secret=True)
        elif auth_choice == "basic":
            username = _prompt_value("Username")
            password = _prompt_value("Password", secret=True)
        with _console.status("Validating Alertmanager integration...", spinner="dots"):
            result = validate_alertmanager_integration(
                base_url=base_url,
                bearer_token=bearer_token,
                username=username,
                password=password,
            )
        _render_integration_result("Alertmanager", result)
        if result.ok:
            creds: dict[str, str] = {"base_url": base_url}
            if bearer_token:
                creds["bearer_token"] = bearer_token
            if username:
                creds["username"] = username
                creds["password"] = password
            upsert_integration("alertmanager", {"credentials": creds})
            env_path = sync_env_values({})
            return "Alertmanager", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_opsgenie() -> tuple[str, str]:
    _, credentials = _integration_defaults("opsgenie")
    while True:
        api_key = _prompt_value(
            "OpsGenie API key (Settings > API key management)",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        region = _prompt_value(
            "OpsGenie region (us or eu)",
            default=_string_value(credentials.get("region"), "us"),
        )
        with _console.status("Validating OpsGenie integration...", spinner="dots"):
            result = validate_opsgenie_integration(api_key=api_key, region=region)
        _render_integration_result("OpsGenie", result)
        if result.ok:
            upsert_integration(
                "opsgenie",
                {"credentials": {"api_key": api_key, "region": region}},
            )
            env_path = sync_env_values({})
            return "OpsGenie", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_pagerduty() -> tuple[str, str]:
    _, credentials = _integration_defaults("pagerduty")
    while True:
        api_key = _prompt_value(
            "PagerDuty API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        base_url = _prompt_value(
            "PagerDuty API base URL (press Enter to use default)",
            default=_string_value(credentials.get("base_url"), "https://api.pagerduty.com"),
        )
        with _console.status("Validating PagerDuty integration...", spinner="dots"):
            result = validate_pagerduty_integration(api_key=api_key, base_url=base_url)
        _render_integration_result("PagerDuty", result)
        if result.ok:
            upsert_integration(
                "pagerduty",
                {"credentials": {"api_key": api_key, "base_url": base_url}},
            )
            env_path = sync_env_values({})
            return "PagerDuty", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_incident_io() -> tuple[str, str]:
    _, credentials = _integration_defaults("incident_io")
    while True:
        api_key = _prompt_value(
            "incident.io API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        base_url = _prompt_value(
            "API base URL override (optional)",
            default=_string_value(credentials.get("base_url")),
            allow_empty=True,
        )
        with _console.status("Validating incident.io integration...", spinner="dots"):
            result = validate_incident_io_integration(
                api_key=api_key,
                base_url=base_url,
            )
        _render_integration_result("incident.io", result)
        if result.ok:
            credentials_payload = {
                "api_key": api_key,
                "base_url": base_url,
            }
            upsert_integration("incident_io", {"credentials": credentials_payload})
            sync_env_secret("INCIDENT_IO_API_KEY", api_key)
            env_path = sync_env_values(
                {
                    "INCIDENT_IO_BASE_URL": base_url,
                }
            )
            return "incident.io", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
