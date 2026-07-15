"""Configurator handler for the Jenkins integration."""

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
from surfaces.cli.wizard.integration_health import validate_jenkins_integration


def _configure_jenkins() -> tuple[str, str]:
    _, credentials = _integration_defaults("jenkins")

    while True:
        base_url = _prompt_value(
            "Jenkins URL (e.g. http://localhost:8080)",
            default=_string_value(credentials.get("base_url")),
        )
        username = _prompt_value(
            "Jenkins username",
            default=_string_value(credentials.get("username")),
        )
        api_token = _prompt_value(
            "Jenkins API token",
            default=_string_value(credentials.get("api_token")),
            secret=True,
        )

        with _console.status("Validating Jenkins integration...", spinner="dots"):
            result = validate_jenkins_integration(
                base_url=base_url, username=username, api_token=api_token
            )
        _render_integration_result("Jenkins", result)
        if result.ok:
            credentials = {"base_url": base_url, "username": username, "api_token": api_token}
            upsert_integration("jenkins", {"credentials": credentials})
            sync_env_secret("JENKINS_API_TOKEN", api_token)
            env_path = sync_env_values(
                {
                    "JENKINS_URL": base_url,
                    "JENKINS_USER": username,
                }
            )
            return "Jenkins", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
