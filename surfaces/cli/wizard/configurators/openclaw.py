"""Configurator handler for the OpenClaw MCP integration."""

from __future__ import annotations

from urllib.parse import urlparse

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
from surfaces.cli.wizard.env_sync import sync_env_secret, sync_env_values
from surfaces.cli.wizard.integration_health import validate_openclaw_integration

DEFAULT_OPENCLAW_MCP_URL = "http://127.0.0.1:18789/"
DEFAULT_OPENCLAW_MCP_MODE = "stdio"
DEFAULT_OPENCLAW_MCP_COMMAND = "openclaw"
DEFAULT_OPENCLAW_MCP_ARGS = ("mcp", "serve")


def _looks_like_openclaw_control_ui_url(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return False

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    return port == 18789 and parsed.path.rstrip("/") == ""


def _configure_openclaw() -> tuple[str, str]:
    _, credentials = _integration_defaults("openclaw")
    stored_command = _string_value(credentials.get("command"))
    stored_args = credentials.get("args")
    use_stdio_defaults = _looks_like_openclaw_control_ui_url(credentials.get("url")) or (
        stored_command == "openclaw-mcp"
        and not _joined_values(stored_args, separator=" ", fallback="")
    )
    while True:
        # Transport is fixed to stdio (the local OpenClaw bridge). In practice it is
        # the only mode anyone selects, so the transport prompt was removed on purpose
        # — do NOT reintroduce a transport selection or a remote branch here.
        mode = DEFAULT_OPENCLAW_MCP_MODE

        url = ""
        command = ""
        args: list[str] = []
        auth_token = ""
        if mode == "stdio":
            command = _prompt_value(
                "OpenClaw bridge command",
                default=(
                    DEFAULT_OPENCLAW_MCP_COMMAND
                    if use_stdio_defaults
                    else _string_value(credentials.get("command"), DEFAULT_OPENCLAW_MCP_COMMAND)
                ),
            )
            args_raw = _prompt_value(
                "OpenClaw bridge args",
                default=(
                    " ".join(DEFAULT_OPENCLAW_MCP_ARGS)
                    if use_stdio_defaults
                    else _joined_values(
                        credentials.get("args"),
                        separator=" ",
                        fallback=" ".join(DEFAULT_OPENCLAW_MCP_ARGS),
                    )
                ),
                allow_empty=True,
            )
            args = [part for part in args_raw.split() if part]
        else:
            url = _prompt_value(
                "OpenClaw bridge URL",
                default=_string_value(credentials.get("url"), DEFAULT_OPENCLAW_MCP_URL),
            )
            auth_token = _prompt_value(
                "OpenClaw auth token (optional)",
                default=_string_value(credentials.get("auth_token")),
                secret=True,
                allow_empty=True,
            )

        credentials = {
            **credentials,
            "url": url,
            "mode": mode,
            "auth_token": auth_token,
            "command": command,
            "args": args,
        }

        with _console.status("Validating OpenClaw bridge...", spinner="dots"):
            result = validate_openclaw_integration(
                url=url,
                mode=mode,
                auth_token=auth_token,
                command=command,
                args=args,
            )
        _render_integration_result("OpenClaw", result)
        if result.ok:
            credentials_dict = {
                "url": url,
                "mode": mode,
                "auth_token": auth_token,
                "command": command,
                "args": args,
            }
            upsert_integration("openclaw", {"credentials": credentials_dict})
            sync_env_secret("OPENCLAW_MCP_AUTH_TOKEN", auth_token)
            env_path = sync_env_values(
                {
                    "OPENCLAW_MCP_URL": url,
                    "OPENCLAW_MCP_MODE": mode,
                    "OPENCLAW_MCP_COMMAND": command,
                    "OPENCLAW_MCP_ARGS": " ".join(args),
                }
            )
            _console.print(f"[{HIGHLIGHT}]OpenClaw · ready[/]")
            _console.print(
                f"[{SECONDARY}]Verify:[/] [bold]uv run opensre integrations verify openclaw[/]"
            )
            _console.print(
                f"[{SECONDARY}]Smoke test:[/] [bold]uv run opensre investigate -i tests/fixtures/openclaw_test_alert.json[/]"
            )
            _console.print(
                f"[{SECONDARY}]Accurate RCA:[/] [bold]also configure Grafana/Datadog and GitHub[/]"
            )
            return "OpenClaw", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
