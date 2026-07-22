"""Configurator handlers for the Sentry and Sentry MCP integrations."""

from __future__ import annotations

from config.env_file import sync_env_values
from integrations.sentry import get_sentry_auth_recommendations
from integrations.sentry_mcp.setup import SENTRY_MCP_SETUP
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
from surfaces.cli.wizard.integration_health import validate_sentry_integration

DEFAULT_SENTRY_URL = "https://sentry.io"


def _configure_sentry_mcp() -> tuple[str, str]:
    return configure_from_spec(SENTRY_MCP_SETUP, title="Sentry MCP")


def _configure_sentry() -> tuple[str, str]:
    _, credentials = _integration_defaults("sentry")
    guidance = get_sentry_auth_recommendations()
    _console.print(
        f"[{SECONDARY}]Recommended: "
        f"{guidance['recommended_token_type']} from {guidance['where_to_create']}. "
        f"{guidance['fallback_token_type']} only if you need broader scopes.[/]"
    )

    while True:
        base_url = _prompt_value(
            "Sentry base URL",
            default=_string_value(credentials.get("base_url"), DEFAULT_SENTRY_URL),
        )
        organization_slug = _prompt_value(
            "Sentry organization slug",
            default=_string_value(credentials.get("organization_slug")),
        )
        project_slug = _prompt_value(
            "Sentry project slug (optional)",
            default=_string_value(credentials.get("project_slug")),
            allow_empty=True,
        )
        auth_token = _prompt_value(
            "Sentry auth token",
            default=_string_value(credentials.get("auth_token")),
            secret=True,
        )

        with _console.status("Validating Sentry integration...", spinner="dots"):
            result = validate_sentry_integration(
                base_url=base_url,
                organization_slug=organization_slug,
                auth_token=auth_token,
                project_slug=project_slug,
            )
        _render_integration_result("Sentry", result)
        if result.ok:
            credentials = {
                "base_url": base_url,
                "organization_slug": organization_slug,
                "auth_token": auth_token,
                "project_slug": project_slug,
            }
            upsert_integration("sentry", {"credentials": credentials})
            env_path = sync_env_values(
                {
                    "SENTRY_URL": base_url,
                    "SENTRY_ORG_SLUG": organization_slug,
                    "SENTRY_PROJECT_SLUG": project_slug,
                }
            )
            return "Sentry", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
