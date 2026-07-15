"""Configurator handler for the GitLab integration."""

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
from surfaces.cli.wizard.integration_health import validate_gitlab_integration

DEFAULT_GITLAB_BASE_URL = "https://gitlab.com/api/v4"


def _configure_gitlab() -> tuple[str, str]:
    _, credentials = _integration_defaults("gitlab")

    while True:
        base_url = _prompt_value(
            "Gitlab base URL",
            default=_string_value(credentials.get("base_url"), DEFAULT_GITLAB_BASE_URL),
        )
        auth_token = _prompt_value(
            "Gitlab access token",
            default=_string_value(credentials.get("auth_token")),
            secret=True,
        )

        with _console.status("Validating Gitlab integration...", spinner="dots"):
            result = validate_gitlab_integration(base_url=base_url, auth_token=auth_token)
        _render_integration_result("Gitlab", result)
        if result.ok:
            credentials = {"base_url": base_url, "auth_token": auth_token}
            upsert_integration("gitlab", {"credentials": credentials})
            sync_env_secret("GITLAB_ACCESS_TOKEN", auth_token)
            env_path = sync_env_values(
                {
                    "GITLAB_BASE_URL": base_url,
                }
            )
            return "Gitlab", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
