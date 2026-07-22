"""Configurator handlers for the Sentry and Sentry MCP integrations."""

from __future__ import annotations

from config.env_file import sync_env_secret, sync_env_values
from integrations.sentry import get_sentry_auth_recommendations
from integrations.sentry.setup import SENTRY_SETUP
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
from surfaces.cli.wizard.integration_health import validate_sentry_mcp_integration

DEFAULT_SENTRY_MCP_URL = "https://mcp.sentry.dev/mcp"
DEFAULT_SENTRY_MCP_MODE = "streamable-http"


def _configure_sentry_mcp() -> tuple[str, str]:
    _, credentials = _integration_defaults("sentry_mcp")

    while True:
        # Transport is fixed to Streamable HTTP (the hosted Sentry MCP server). In
        # practice it is the only mode anyone selects, so the transport prompt was
        # removed on purpose — do NOT reintroduce a transport selection here.
        mode = DEFAULT_SENTRY_MCP_MODE

        url = ""
        command = ""
        args: list[str] = []
        if mode == "stdio":
            command = _prompt_value(
                "Sentry MCP command",
                default=_string_value(credentials.get("command"), "npx"),
            )
            args_raw = _prompt_value(
                "Sentry MCP args",
                default=_joined_values(
                    credentials.get("args"),
                    separator=" ",
                    fallback="@sentry/mcp-server@latest",
                ),
                allow_empty=True,
            )
            args = [part for part in args_raw.split() if part]
        else:
            url = _prompt_value(
                "Sentry MCP URL",
                default=_string_value(credentials.get("url"), DEFAULT_SENTRY_MCP_URL),
            )

        auth_token = _prompt_value(
            "Sentry user auth token",
            default=_string_value(credentials.get("auth_token")),
            secret=True,
        )
        if mode != "stdio" and not auth_token:
            _console.print(
                f"[{SECONDARY}]A user auth token is required for the hosted Sentry MCP server.[/]"
            )
            continue

        host = _prompt_value(
            "Self-hosted Sentry host (optional)",
            default=_string_value(credentials.get("host")),
            allow_empty=True,
        )

        with _console.status("Validating Sentry MCP...", spinner="dots"):
            result = validate_sentry_mcp_integration(
                url=url,
                mode=mode,
                auth_token=auth_token,
                command=command,
                args=args,
                host=host,
            )
        _render_integration_result("Sentry MCP", result)
        if result.ok:
            credentials_dict = {
                "url": url,
                "mode": mode,
                "auth_token": auth_token,
                "command": command,
                "args": args,
                "host": host,
            }
            upsert_integration("sentry_mcp", {"credentials": credentials_dict})
            sync_env_secret("SENTRY_MCP_AUTH_TOKEN", auth_token)
            env_path = sync_env_values(
                {
                    "SENTRY_MCP_URL": url,
                    "SENTRY_MCP_MODE": mode,
                    "SENTRY_MCP_COMMAND": command,
                    "SENTRY_MCP_ARGS": " ".join(args),
                    "SENTRY_MCP_HOST": host,
                }
            )
            _console.print(f"[{HIGHLIGHT}]Sentry MCP · ready[/]")
            _console.print(
                f"[{SECONDARY}]Verify:[/] [bold]uv run opensre integrations verify sentry_mcp[/]"
            )
            return "Sentry MCP", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_sentry() -> tuple[str, str]:
    guidance = get_sentry_auth_recommendations()
    return configure_from_spec(
        SENTRY_SETUP,
        title="Sentry",
        intro=(
            f"[{SECONDARY}]Recommended: "
            f"{guidance['recommended_token_type']} from {guidance['where_to_create']}. "
            f"{guidance['fallback_token_type']} only if you need broader scopes.[/]"
        ),
    )
