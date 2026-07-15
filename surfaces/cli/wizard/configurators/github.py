"""Configurator handlers for the GitHub MCP integration."""

from __future__ import annotations

from integrations.store import upsert_integration
from platform.terminal.theme import DEVICE_CODE, SECONDARY
from surfaces.cli.wizard._ui import (
    Choice,
    _choose,
    _console,
    _integration_defaults,
    _joined_values,
    _parse_csv_values,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.env_sync import sync_env_values
from surfaces.cli.wizard.integration_health import validate_github_mcp_integration

DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
DEFAULT_GITHUB_MCP_MODE = "streamable-http"


def _github_wizard_browser_authorize() -> str | None:
    """Run GitHub device-flow browser authorization inside the wizard."""
    from rich.markup import escape

    from integrations.github.mcp_oauth import (
        GitHubDeviceCode,
        GitHubDeviceFlowError,
        authorize_github_via_device_flow,
    )

    def _show(code: GitHubDeviceCode) -> None:
        user_code = escape(code.user_code)
        _console.print()
        _console.print(f"  1. Your browser will open [bold]{code.verification_uri}[/]")
        _console.print(f"     [{SECONDARY}](if it doesn't open, visit that URL yourself).[/]")
        _console.print(
            f"  2. Enter this one-time code when GitHub asks: [{DEVICE_CODE}]{user_code}[/]"
        )
        _console.print("  3. Approve the request for OpenSRE.")
        _console.print()
        _console.print(f"  [{SECONDARY}]Waiting for you to approve in the browser…[/]")

    _console.print()
    _console.print("Sign in to GitHub in your browser (device authorization):")
    _console.print(f"[{SECONDARY}]Requesting a one-time code from GitHub…[/]")
    try:
        token = authorize_github_via_device_flow(on_prompt=_show)
    except GitHubDeviceFlowError as err:
        _console.print(f"Browser authorization unavailable: {err}")
        return None
    except Exception as err:  # network/transport issues
        _console.print(f"Browser authorization failed: {err}")
        return None
    _console.print("[bold]Authorized.[/] Saved a GitHub token from the browser sign-in.")
    return token.access_token


def _github_wizard_auth_token(mode: str, credentials: object) -> str:
    """Resolve a GitHub MCP auth token, offering browser sign-in for remote modes."""
    from collections.abc import Mapping

    creds = credentials if isinstance(credentials, Mapping) else {}
    existing = _string_value(creds.get("auth_token"))
    if mode == "stdio":
        return _prompt_value(
            "GitHub PAT / auth token (optional if the server already authenticates upstream)",
            default=existing,
            secret=True,
            allow_empty=True,
        )

    method = _choose(
        "How do you want to connect OpenSRE to GitHub?",
        [
            Choice(
                value="browser",
                label="Sign in with GitHub in your browser (opens a page, enter a one-time code)",
            ),
            Choice(value="token", label="Paste a personal access token (PAT)"),
            Choice(value="none", label="Skip — the MCP server authenticates upstream"),
        ],
        default="browser",
    )
    if method == "none":
        return ""
    if method == "browser":
        token = _github_wizard_browser_authorize()
        if token:
            return token
        _console.print("Falling back to manual token entry.")
    return _prompt_value(
        "GitHub PAT / auth token",
        default=existing,
        secret=True,
        allow_empty=True,
    )


def _configure_github_mcp() -> tuple[str, str]:
    _, credentials = _integration_defaults("github")
    # Transport is fixed to Streamable HTTP — the only mode anyone selects in practice,
    # and SSE/stdio are deprecated for the hosted GitHub MCP server. The transport
    # prompt was removed on purpose — do NOT reintroduce a transport selection here.
    mode = DEFAULT_GITHUB_MCP_MODE

    while True:
        url = ""
        command = ""
        args: list[str] = []
        if mode == "stdio":
            command = _prompt_value(
                "GitHub MCP command",
                default=_string_value(credentials.get("command"), "github-mcp-server"),
            )
            args_raw = _prompt_value(
                "GitHub MCP args",
                default=_joined_values(
                    credentials.get("args"),
                    separator=" ",
                    fallback="stdio --toolsets repos,issues,pull_requests,actions,search",
                ),
            )
            args = [part for part in args_raw.split() if part]
        else:
            url = _prompt_value(
                "GitHub MCP URL",
                default=_string_value(credentials.get("url"), DEFAULT_GITHUB_MCP_URL),
            )

        toolsets = _parse_csv_values(
            _prompt_value(
                "GitHub MCP toolsets (comma-separated)",
                default=_joined_values(
                    credentials.get("toolsets"),
                    separator=",",
                    fallback="repos,issues,pull_requests,actions,search",
                ),
            )
        )
        auth_token = _github_wizard_auth_token(mode, credentials)

        repo_view = _choose(
            "Which repository view should we use to verify access?",
            [
                Choice(value="auto", label="Auto (recommended)"),
                Choice(value="user", label="Your repositories"),
                Choice(value="starred", label="Starred repositories"),
                Choice(value="search_user", label="Search: user:<your_login>"),
            ],
            default="auto",
        )
        repo_visibility = _choose(
            "Filter repositories by visibility (best-effort)",
            [
                Choice(value="any", label="Any (recommended)"),
                Choice(value="public", label="Public only"),
                Choice(value="private", label="Private only"),
            ],
            default="any",
        )

        with _console.status("Validating GitHub MCP integration...", spinner="dots"):
            result = validate_github_mcp_integration(
                url=url,
                mode=mode,
                auth_token=auth_token,
                command=command,
                args=args,
                toolsets=toolsets,
                repo_view=repo_view,
                repo_visibility=repo_visibility,
            )
        display_level = "standard"
        if result.ok:
            display_level = _choose(
                "How should we show repository access?",
                [
                    Choice(value="summary", label="Brief (recommended) — no repo names"),
                    Choice(
                        value="standard",
                        label="Standard — scope summary only",
                    ),
                    Choice(
                        value="full",
                        label="Expanded — include repo names",
                    ),
                ],
                default="summary",
            )
        _render_integration_result(
            "GitHub MCP",
            result,
            github_display_level=display_level,
        )
        if result.ok:
            credentials = {
                "url": url,
                "mode": mode,
                "auth_token": auth_token,
                "command": command,
                "args": args,
                "toolsets": toolsets,
            }
            authenticated_user = ""
            if result.github_mcp is not None:
                authenticated_user = (result.github_mcp.authenticated_user or "").strip()
            if authenticated_user:
                credentials["username"] = authenticated_user
            upsert_integration("github", {"credentials": credentials})
            if authenticated_user:
                from platform.analytics.cli import identify_github_username

                identify_github_username(authenticated_user)
            env_path = sync_env_values(
                {
                    "GITHUB_MCP_URL": url,
                    "GITHUB_MCP_MODE": mode,
                    "GITHUB_MCP_COMMAND": command,
                    "GITHUB_MCP_ARGS": " ".join(args),
                    "GITHUB_MCP_TOOLSETS": ",".join(toolsets),
                }
            )
            return "GitHub MCP", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
