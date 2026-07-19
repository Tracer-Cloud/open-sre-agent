"""Configurator handler for the Dagster integration."""

from __future__ import annotations

from integrations.store import upsert_integration
from platform.terminal.theme import SECONDARY
from surfaces.cli.wizard._ui import (
    _console,
    _integration_defaults,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.env_sync import sync_env_secret, sync_env_values
from surfaces.cli.wizard.integration_health import validate_dagster_integration


def _configure_dagster() -> tuple[str, str]:
    _, credentials = _integration_defaults("dagster")
    _console.print("\n[bold]Dagster Integration[/bold]")
    _console.print(
        f"[{SECONDARY}]Dagster webserver URL. "
        f"OSS local dev: http://localhost:3000. "
        f"Dagster+: https://<deployment>.dagster.cloud/<env>. "
        f"API token required for Dagster+; leave blank for unauthenticated OSS.[/]\n"
    )
    while True:
        endpoint = _prompt_value(
            "Dagster webserver URL",
            default=_string_value(credentials.get("endpoint"), "http://localhost:3000"),
        )
        api_token = _prompt_value(
            "Dagster API token (optional for OSS)",
            default=_string_value(credentials.get("api_token")),
            secret=True,
            allow_empty=True,
        )
        with _console.status("Validating Dagster integration...", spinner="dots"):
            result = validate_dagster_integration(endpoint=endpoint, api_token=api_token)
        _render_integration_result("Dagster", result)
        if result.ok:
            upsert_integration(
                "dagster",
                {"credentials": {"endpoint": endpoint, "api_token": api_token}},
            )
            if api_token:
                sync_env_secret("DAGSTER_API_TOKEN", api_token)
            env_path = sync_env_values({"DAGSTER_ENDPOINT": endpoint})
            return "Dagster", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
