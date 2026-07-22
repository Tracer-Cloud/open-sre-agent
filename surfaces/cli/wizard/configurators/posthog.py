"""Configurator handlers for the PostHog and PostHog MCP integrations."""

from __future__ import annotations

from config.env_file import sync_env_secret, sync_env_values
from integrations.posthog.setup import POSTHOG_SETUP
from integrations.store import upsert_integration
from platform.terminal.theme import HIGHLIGHT, SECONDARY
from surfaces.cli.wizard._ui import (
    _console,
    _integration_defaults,
    _joined_values,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec
from surfaces.cli.wizard.integration_health import validate_posthog_mcp_integration

DEFAULT_POSTHOG_MCP_URL = "https://mcp.posthog.com/mcp"
DEFAULT_POSTHOG_MCP_MODE = "streamable-http"


def _configure_posthog() -> tuple[str, str]:
    return configure_from_spec(
        POSTHOG_SETUP,
        title="PostHog",
        intro=(
            f"[{SECONDARY}]Create a personal API key (phx_...) with read access — "
            "https://posthog.com/docs/api/personal-api-keys[/]"
        ),
    )


def _configure_posthog_mcp() -> tuple[str, str]:
    _, credentials = _integration_defaults("posthog_mcp")

    while True:
        # Transport is fixed to Streamable HTTP (the hosted PostHog MCP server). In
        # practice it is the only mode anyone selects, so the transport prompt was
        # removed on purpose — do NOT reintroduce a transport selection here.
        mode = DEFAULT_POSTHOG_MCP_MODE

        url = ""
        command = ""
        args: list[str] = []
        if mode == "stdio":
            command = _prompt_value(
                "PostHog MCP command",
                default=_string_value(credentials.get("command"), "npx"),
            )
            args_raw = _prompt_value(
                "PostHog MCP args",
                default=_joined_values(
                    credentials.get("args"),
                    separator=" ",
                    fallback="-y @posthog/mcp-server@latest",
                ),
                allow_empty=True,
            )
            args = [part for part in args_raw.split() if part]
        else:
            url = _prompt_value(
                "PostHog MCP URL",
                default=_string_value(credentials.get("url"), DEFAULT_POSTHOG_MCP_URL),
            )

        auth_token = _prompt_value(
            "PostHog personal API key (MCP Server preset)",
            default=_string_value(credentials.get("auth_token")),
            secret=True,
        )
        project_id = _prompt_value(
            "PostHog project ID (optional)",
            default=_string_value(credentials.get("project_id")),
            allow_empty=True,
        )

        credentials = {
            **credentials,
            "url": url,
            "mode": mode,
            "auth_token": auth_token,
            "command": command,
            "args": args,
            "project_id": project_id,
            "read_only": True,
        }

        with _console.status("Validating PostHog MCP...", spinner="dots"):
            result = validate_posthog_mcp_integration(
                url=url,
                mode=mode,
                auth_token=auth_token,
                command=command,
                args=args,
                project_id=project_id,
                read_only=True,
            )
        _render_integration_result("PostHog MCP", result)
        if result.ok:
            credentials_dict = {
                "url": url,
                "mode": mode,
                "auth_token": auth_token,
                "command": command,
                "args": args,
                "project_id": project_id,
                "read_only": True,
            }
            upsert_integration("posthog_mcp", {"credentials": credentials_dict})
            sync_env_secret("POSTHOG_MCP_AUTH_TOKEN", auth_token)
            env_path = sync_env_values(
                {
                    "POSTHOG_MCP_URL": url,
                    "POSTHOG_MCP_MODE": mode,
                    "POSTHOG_MCP_COMMAND": command,
                    "POSTHOG_MCP_ARGS": " ".join(args),
                    "POSTHOG_MCP_PROJECT_ID": project_id,
                }
            )
            _console.print(f"[{HIGHLIGHT}]PostHog MCP · ready[/]")
            _console.print(
                f"[{SECONDARY}]Verify:[/] [bold]uv run opensre integrations verify posthog_mcp[/]"
            )
            return "PostHog MCP", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
