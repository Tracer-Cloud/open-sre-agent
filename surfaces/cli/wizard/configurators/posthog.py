"""Configurator handlers for the PostHog and PostHog MCP integrations."""

from __future__ import annotations

from config.constants.posthog import DEFAULT_POSTHOG_URL
from config.env_file import sync_env_secret, sync_env_values
from integrations.posthog_mcp.setup import POSTHOG_MCP_SETUP
from integrations.store import upsert_integration
from platform.terminal.theme import SECONDARY
from surfaces.cli.wizard._ui import (
    _console,
    _integration_defaults,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec
from surfaces.cli.wizard.integration_health import validate_posthog_integration


def _configure_posthog() -> tuple[str, str]:
    _, credentials = _integration_defaults("posthog")
    _console.print(
        f"[{SECONDARY}]Create a personal API key (phx_...) with read access — "
        "https://posthog.com/docs/api/personal-api-keys[/]"
    )

    while True:
        base_url = _prompt_value(
            "PostHog API base URL",
            default=_string_value(credentials.get("base_url"), DEFAULT_POSTHOG_URL),
        )
        project_id = _prompt_value(
            "PostHog project ID",
            default=_string_value(credentials.get("project_id")),
        )
        personal_api_key = _prompt_value(
            "PostHog personal API key",
            default=_string_value(credentials.get("personal_api_key")),
            secret=True,
        )

        with _console.status("Validating PostHog integration...", spinner="dots"):
            result = validate_posthog_integration(
                base_url=base_url,
                project_id=project_id,
                personal_api_key=personal_api_key,
            )
        _render_integration_result("PostHog", result)
        if result.ok:
            credentials = {
                "base_url": base_url,
                "project_id": project_id,
                "personal_api_key": personal_api_key,
            }
            upsert_integration("posthog", {"credentials": credentials})
            sync_env_secret("POSTHOG_PERSONAL_API_KEY", personal_api_key)
            env_path = sync_env_values(
                {
                    "POSTHOG_PROJECT_ID": project_id,
                    "POSTHOG_BASE_URL": base_url,
                }
            )
            _console.print(
                f"[{SECONDARY}]Verify:[/] [bold]uv run opensre integrations verify posthog[/]"
            )
            return "PostHog", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_posthog_mcp() -> tuple[str, str]:
    return configure_from_spec(POSTHOG_MCP_SETUP, title="PostHog MCP")
