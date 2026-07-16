"""Configurator handler for the Vercel integration."""

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
from surfaces.cli.wizard.env_sync import sync_env_values
from surfaces.cli.wizard.integration_health import validate_vercel_integration


def _configure_vercel() -> tuple[str, str]:
    _, credentials = _integration_defaults("vercel")
    while True:
        api_token = _prompt_value(
            "Vercel API token (Account Settings > Tokens)",
            default=_string_value(credentials.get("api_token")),
            secret=True,
        )
        team_id = _prompt_value(
            "Vercel team ID (optional, for team-scoped access)",
            default=_string_value(credentials.get("team_id")),
            allow_empty=True,
        )
        with _console.status("Validating Vercel integration...", spinner="dots"):
            result = validate_vercel_integration(api_token=api_token, team_id=team_id)
        _render_integration_result("Vercel", result)
        if result.ok:
            upsert_integration(
                "vercel",
                {"credentials": {"api_token": api_token, "team_id": team_id}},
            )
            env_path = sync_env_values({})
            return "Vercel", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
