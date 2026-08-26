"""Configurator handlers for the GitHub MCP integration.

Collection stays custom (browser OAuth + optional repo-scope probes). Persist
goes through :func:`integrations.setup_flow.apply_setup` so the token lands in
the keyring and the non-secrets in ``.env``, not just the store.
"""

from __future__ import annotations

import dataclasses

from infrastructure.terminal.theme import DEVICE_CODE, SECONDARY
from integrations.github import DEFAULT_GITHUB_MCP_MODE, DEFAULT_GITHUB_MCP_URL
from integrations.github.setup import GITHUB_SETUP
from integrations.setup_flow import apply_setup
from surfaces.cli.wizard.components import (
    Choice,
    choose,
    console,
    integration_defaults,
    joined_values,
    parse_csv_values,
    prompt_value,
    string_value,
)
from surfaces.cli.wizard.integration_health import validate_github_mcp_integration
from surfaces.cli.wizard.integration_validators.shared import IntegrationHealthResult
from surfaces.cli.wizard.summaries import render_integration_result


def _github_wizard_browser_authorize() -> str | None:
    """Run GitHub device-flow browser authorization inside the wizard."""
    from rich.markup import escape

    from integrations.github import (
        GitHubDeviceCode,
        GitHubDeviceFlowError,
        authorize_github_via_device_flow,
    )

    def _show(code: GitHubDeviceCode) -> None:
        user_code = escape(code.user_code)
        console.print()
        console.print(f"  1. Your browser will open [bold]{code.verification_uri}[/]")
        console.print(f"     [{SECONDARY}](if it doesn't open, visit that URL yourself).[/]")
        console.print(
            f"  2. Enter this one-time code when GitHub asks: [{DEVICE_CODE}]{user_code}[/]"
        )
        console.print("  3. Approve the request for OpenSRE.")
        console.print()
        console.print(f"  [{SECONDARY}]Waiting for you to approve in the browser…[/]")

    console.print()
    console.print("Sign in to GitHub in your browser (device authorization):")
    console.print(f"[{SECONDARY}]Requesting a one-time code from GitHub…[/]")
    try:
        token = authorize_github_via_device_flow(on_prompt=_show)
    except GitHubDeviceFlowError as err:
        console.print(f"Browser authorization unavailable: {err}")
        return None
    except Exception as err:  # network/transport issues
        console.print(f"Browser authorization failed: {err}")
        return None
    console.print("[bold]Authorized.[/] Saved a GitHub token from the browser sign-in.")
    return token.access_token


def _github_wizard_auth_token(mode: str, credentials: object) -> str:
    """Resolve a GitHub MCP auth token, offering browser sign-in for remote modes."""
    from collections.abc import Mapping

    creds = credentials if isinstance(credentials, Mapping) else {}
    existing = string_value(creds.get("auth_token"))
    if mode == "stdio":
        return prompt_value(
            "GitHub PAT / auth token (optional if the server already authenticates upstream)",
            default=existing,
            secret=True,
            allow_empty=True,
        )

    method = choose(
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
        console.print("Falling back to manual token entry.")
    return prompt_value(
        "GitHub PAT / auth token",
        default=existing,
        secret=True,
        allow_empty=True,
    )


def _configure_github_mcp() -> tuple[str, str]:
    _, credentials = integration_defaults("github")
    # Transport is fixed to Streamable HTTP — the only mode anyone selects in practice,
    # and SSE/stdio are deprecated for the hosted GitHub MCP server. The transport
    # prompt was removed on purpose — do NOT reintroduce a transport selection here.
    mode = DEFAULT_GITHUB_MCP_MODE

    while True:
        url = prompt_value(
            "GitHub MCP URL",
            default=string_value(credentials.get("url"), DEFAULT_GITHUB_MCP_URL),
        )
        toolsets = parse_csv_values(
            prompt_value(
                "GitHub MCP toolsets (comma-separated)",
                default=joined_values(
                    credentials.get("toolsets"),
                    separator=",",
                    fallback="repos,issues,pull_requests,actions,search",
                ),
            )
        )
        auth_token = _github_wizard_auth_token(mode, credentials)

        repo_view = choose(
            "Which repository view should we use to verify access?",
            [
                Choice(value="auto", label="Auto (recommended)"),
                Choice(value="user", label="Your repositories"),
                Choice(value="starred", label="Starred repositories"),
                Choice(value="search_user", label="Search: user:<your_login>"),
            ],
            default="auto",
        )
        repo_visibility = choose(
            "Filter repositories by visibility (best-effort)",
            [
                Choice(value="any", label="Any (recommended)"),
                Choice(value="public", label="Public only"),
                Choice(value="private", label="Private only"),
            ],
            default="any",
        )

        with console.status("Validating GitHub MCP integration...", spinner="dots"):
            result = validate_github_mcp_integration(
                url=url,
                mode=mode,
                auth_token=auth_token,
                command="",
                args=[],
                toolsets=toolsets,
                repo_view=repo_view,
                repo_visibility=repo_visibility,
            )
        display_level = "standard"
        if result.ok:
            display_level = choose(
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
        render_integration_result(
            "GitHub MCP",
            result,
            github_display_level=display_level,
        )
        if result.ok:
            authenticated_user = ""
            if result.github_mcp is not None:
                authenticated_user = (result.github_mcp.authenticated_user or "").strip()
            # Already verified above (with optional repo-scope probes). Skip the
            # spec's simpler probe so we do not hit the hosted server twice.
            outcome = apply_setup(
                dataclasses.replace(GITHUB_SETUP, verify=None),
                {
                    "mode": mode,
                    "url": url,
                    "auth_token": auth_token,
                    "toolsets": ",".join(toolsets),
                    "username": authenticated_user,
                },
            )
            render_integration_result(
                "GitHub MCP",
                IntegrationHealthResult(ok=outcome.ok, detail=outcome.detail),
            )
            if outcome.ok:
                assert outcome.env_path is not None, (
                    "apply_setup returned ok=True without an env_path"
                )
                return "GitHub MCP", str(outcome.env_path)
        console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")
