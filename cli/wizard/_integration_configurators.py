"""Integration configurator handlers for the wizard onboarding flow."""

from __future__ import annotations

from urllib.parse import urlparse

from cli.interactive_shell.ui.theme import (
    DEVICE_CODE,
    DIM,
    ERROR,
    GLYPH_ERROR,
    HIGHLIGHT,
    SECONDARY,
    WARNING,
)
from cli.wizard._ui import (
    Choice,
    _choose,
    _confirm,
    _console,
    _integration_defaults,
    _joined_values,
    _parse_csv_values,
    _prompt_value,
    _render_integration_result,
    _step,
    _string_value,
)
from cli.wizard.env_sync import sync_env_secret, sync_env_values
from cli.wizard.integration_health import (
    validate_alertmanager_integration,
    validate_aws_integration,
    validate_betterstack_integration,
    validate_coralogix_integration,
    validate_dagster_integration,
    validate_datadog_integration,
    validate_discord_bot,
    validate_github_mcp_integration,
    validate_gitlab_integration,
    validate_google_docs_integration,
    validate_grafana_integration,
    validate_honeycomb_integration,
    validate_incident_io_integration,
    validate_jenkins_integration,
    validate_jira_integration,
    validate_notion_integration,
    validate_openclaw_integration,
    validate_opensearch_integration,
    validate_opsgenie_integration,
    validate_pagerduty_integration,
    validate_posthog_mcp_integration,
    validate_sentry_integration,
    validate_sentry_mcp_integration,
    validate_slack_webhook,
    validate_splunk_integration,
    validate_telegram_bot,
    validate_tempo_integration,
    validate_vercel_integration,
)
from cli.wizard.onboard_integrations import (
    ONBOARD_INTEGRATION_CHOICES,
    ONBOARD_INTEGRATION_GROUP_ORDER,
    ONBOARD_SKIP_CHOICE,
)
from integrations.sentry import get_sentry_auth_recommendations
from integrations.store import remove_integration, upsert_integration

DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
DEFAULT_GITHUB_MCP_MODE = "streamable-http"
DEFAULT_OPENCLAW_MCP_URL = "http://127.0.0.1:18789/"
DEFAULT_OPENCLAW_MCP_MODE = "stdio"
DEFAULT_OPENCLAW_MCP_COMMAND = "openclaw"
DEFAULT_OPENCLAW_MCP_ARGS = ("mcp", "serve")
DEFAULT_POSTHOG_MCP_URL = "https://mcp.posthog.com/mcp"
DEFAULT_POSTHOG_MCP_MODE = "streamable-http"
DEFAULT_SENTRY_MCP_URL = "https://mcp.sentry.dev/mcp"
DEFAULT_SENTRY_MCP_MODE = "streamable-http"
DEFAULT_SENTRY_URL = "https://sentry.io"
DEFAULT_GITLAB_BASE_URL = "https://gitlab.com/api/v4"


def _looks_like_openclaw_control_ui_url(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return False

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    return port == 18789 and parsed.path.rstrip("/") == ""


def _configure_grafana() -> tuple[str, str]:
    _, credentials = _integration_defaults("grafana")
    saved_endpoint = _string_value(credentials.get("endpoint"))
    # Don't pre-fill a localhost URL — it's a local dev default, not a real instance.
    endpoint_default = (
        saved_endpoint if saved_endpoint and "localhost" not in saved_endpoint else ""
    )
    while True:
        endpoint = _prompt_value(
            "Grafana instance URL",
            default=endpoint_default,
        )
        api_key = _prompt_value(
            "Grafana service account token",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        with _console.status("Validating Grafana integration...", spinner="dots"):
            result = validate_grafana_integration(endpoint=endpoint, api_key=api_key)
        _render_integration_result("Grafana", result)
        if result.ok:
            upsert_integration(
                "grafana", {"credentials": {"endpoint": endpoint, "api_key": api_key}}
            )
            env_path = sync_env_values(
                {
                    "GRAFANA_INSTANCE_URL": endpoint,
                }
            )
            return "Grafana", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_grafana_local() -> tuple[str, str]:
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("docker"):
        _console.print(f"[{ERROR}]Docker not found.[/]")
        _console.print(f"[{SECONDARY}]Install Docker Desktop and retry.[/]")
        return "Grafana Local (skipped)", ""

    # Check Docker daemon is actually running
    ping = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if ping.returncode != 0:
        _console.print(f"[{ERROR}]Docker is not running.[/]")
        _console.print(
            f"[{SECONDARY}]Start Docker Desktop, then run [bold]opensre onboard[/bold] again.[/]"
        )
        return "Grafana Local (skipped)", ""

    compose_file = str(Path(__file__).parent / "local_grafana_stack/docker-compose.yml")
    with _console.status("Starting Grafana + Loki (docker compose up -d)...", spinner="dots"):
        result = subprocess.run(
            ["docker", "compose", "-f", compose_file, "up", "-d"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    if result.returncode != 0:
        _console.print(f"[{ERROR}]Docker compose failed.[/]")
        _console.print(result.stderr or result.stdout)
        return "Grafana Local (skipped)", ""

    with _console.status("Waiting for Loki to be ready and seeding logs...", spinner="dots"):
        try:
            from cli.wizard.grafana_seed import seed_logs

            seed_logs()
        except (SystemExit, Exception) as exc:
            _console.print(f"[{ERROR}]Loki seed failed: {exc}[/]")
            return "Grafana Local (skipped)", ""

    endpoint = "http://localhost:3000"
    api_key = ""
    remove_integration("grafana")  # clean up any stale grafana record pointing to localhost
    upsert_integration("grafana_local", {"credentials": {"endpoint": endpoint, "api_key": api_key}})
    env_path = sync_env_values({"GRAFANA_INSTANCE_URL": endpoint})
    _console.print(f"[{HIGHLIGHT}]Grafana Local · ready[/]")
    _console.print(f"[{SECONDARY}]UI: {endpoint}[/]")
    _console.print(f"[{SECONDARY}]Loki seeded with events_fact pipeline failure logs.[/]")
    _console.print(f"[{SECONDARY}]Run RCA:[/]")
    _console.print("[bold]  opensre investigate -i tests/fixtures/grafana_local_alert.json[/]")
    return "Grafana Local", str(env_path)


def _configure_datadog() -> tuple[str, str]:
    _, credentials = _integration_defaults("datadog")
    while True:
        api_key = _prompt_value(
            "Datadog API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        app_key = _prompt_value(
            "Datadog application key",
            default=_string_value(credentials.get("app_key")),
            secret=True,
        )
        site = _prompt_value(
            "Datadog site",
            default=_string_value(credentials.get("site"), "datadoghq.com"),
        )
        with _console.status("Validating Datadog integration...", spinner="dots"):
            result = validate_datadog_integration(api_key=api_key, app_key=app_key, site=site)
        _render_integration_result("Datadog", result)
        if result.ok:
            upsert_integration(
                "datadog",
                {"credentials": {"api_key": api_key, "app_key": app_key, "site": site}},
            )
            env_path = sync_env_values({})
            return "Datadog", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_honeycomb() -> tuple[str, str]:
    _, credentials = _integration_defaults("honeycomb")
    while True:
        api_key = _prompt_value(
            "Honeycomb configuration API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        dataset = _prompt_value(
            "Honeycomb dataset slug or __all__",
            default=_string_value(credentials.get("dataset"), "__all__"),
        )
        base_url = _prompt_value(
            "Honeycomb API URL",
            default=_string_value(credentials.get("base_url"), "https://api.honeycomb.io"),
        )
        with _console.status("Validating Honeycomb integration...", spinner="dots"):
            result = validate_honeycomb_integration(
                api_key=api_key,
                dataset=dataset,
                base_url=base_url,
            )
        _render_integration_result("Honeycomb", result)
        if result.ok:
            upsert_integration(
                "honeycomb",
                {"credentials": {"api_key": api_key, "dataset": dataset, "base_url": base_url}},
            )
            env_path = sync_env_values(
                {
                    "HONEYCOMB_DATASET": dataset,
                    "HONEYCOMB_API_URL": base_url,
                }
            )
            return "Honeycomb", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_coralogix() -> tuple[str, str]:
    _, credentials = _integration_defaults("coralogix")
    while True:
        api_key = _prompt_value(
            "Coralogix DataPrime API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        base_url = _prompt_value(
            "Coralogix API URL",
            default=_string_value(credentials.get("base_url"), "https://api.coralogix.com"),
        )
        application_name = _prompt_value(
            "Coralogix application name (optional)",
            default=_string_value(credentials.get("application_name")),
            allow_empty=True,
        )
        subsystem_name = _prompt_value(
            "Coralogix subsystem name (optional)",
            default=_string_value(credentials.get("subsystem_name")),
            allow_empty=True,
        )
        with _console.status("Validating Coralogix integration...", spinner="dots"):
            result = validate_coralogix_integration(
                api_key=api_key,
                base_url=base_url,
                application_name=application_name,
                subsystem_name=subsystem_name,
            )
        _render_integration_result("Coralogix", result)
        if result.ok:
            upsert_integration(
                "coralogix",
                {
                    "credentials": {
                        "api_key": api_key,
                        "base_url": base_url,
                        "application_name": application_name,
                        "subsystem_name": subsystem_name,
                    }
                },
            )
            env_path = sync_env_values(
                {
                    "CORALOGIX_API_URL": base_url,
                    "CORALOGIX_APPLICATION_NAME": application_name,
                    "CORALOGIX_SUBSYSTEM_NAME": subsystem_name,
                }
            )
            return "Coralogix", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_dagster() -> tuple[str, str]:
    _, credentials = _integration_defaults("dagster")
    _console.print("\n[bold]Dagster Integration[/bold]")
    _console.print(
        f"[{SECONDARY}]Dagster webserver URL. "
        f"OSS local dev: http://localhost:3000. "
        f"Dagster+: https://<deployment>.dagster.cloud/<env>. "
        f"API token required for Dagster+; leave blank for unauthenticated OSS.[/]\n"
    )
    while True:
        endpoint = _prompt_value(
            "Dagster webserver URL",
            default=_string_value(credentials.get("endpoint"), "http://localhost:3000"),
        )
        api_token = _prompt_value(
            "Dagster API token (optional for OSS)",
            default=_string_value(credentials.get("api_token")),
            secret=True,
            allow_empty=True,
        )
        with _console.status("Validating Dagster integration...", spinner="dots"):
            result = validate_dagster_integration(endpoint=endpoint, api_token=api_token)
        _render_integration_result("Dagster", result)
        if result.ok:
            upsert_integration(
                "dagster",
                {"credentials": {"endpoint": endpoint, "api_token": api_token}},
            )
            if api_token:
                sync_env_secret("DAGSTER_API_TOKEN", api_token)
            env_path = sync_env_values({"DAGSTER_ENDPOINT": endpoint})
            return "Dagster", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_slack() -> tuple[str, str]:
    _, credentials = _integration_defaults("slack")
    while True:
        webhook_url = _prompt_value(
            "Slack webhook URL",
            default=_string_value(credentials.get("webhook_url")),
            secret=True,
        )
        with _console.status("Validating Slack webhook...", spinner="dots"):
            result = validate_slack_webhook(webhook_url=webhook_url)
        _render_integration_result("Slack", result)
        if result.ok:
            # Persist the webhook to the store like every other integration
            # (and like the CLI `_setup_slack`). Without this the wizard would
            # validate the webhook, report "Slack" in the success summary, then
            # silently discard it — leaving no readable credential anywhere.
            upsert_integration("slack", {"credentials": {"webhook_url": webhook_url}})
            env_path = sync_env_values({})
            return "Slack", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_aws() -> tuple[str, str]:
    existing, credentials = _integration_defaults("aws")
    default_auth_mode = "role" if _string_value(existing.get("role_arn")) else "keys"
    auth_mode = _choose(
        "Choose the AWS authentication method:",
        [
            Choice(value="role", label="IAM role ARN"),
            Choice(value="keys", label="Access key + secret"),
        ],
        default=default_auth_mode,
    )

    while True:
        region = _prompt_value(
            "AWS region",
            default=_string_value(credentials.get("region"), "us-east-1"),
        )
        if auth_mode == "role":
            role_arn = _prompt_value(
                "IAM role ARN",
                default=_string_value(existing.get("role_arn")),
            )
            external_id = _prompt_value(
                "External ID",
                default=_string_value(existing.get("external_id")),
                allow_empty=True,
            )
            with _console.status("Validating AWS role...", spinner="dots"):
                result = validate_aws_integration(
                    region=region,
                    role_arn=role_arn,
                    external_id=external_id,
                )
            _render_integration_result("AWS", result)
            if result.ok:
                upsert_integration(
                    "aws",
                    {
                        "role_arn": role_arn,
                        "external_id": external_id,
                        "credentials": {"region": region},
                    },
                )
                env_path = sync_env_values({"AWS_REGION": region})
                return "AWS", str(env_path)
        else:
            access_key_id = _prompt_value(
                "AWS access key ID",
                default=_string_value(credentials.get("access_key_id")),
                secret=True,
            )
            secret_access_key = _prompt_value(
                "AWS secret access key",
                default=_string_value(credentials.get("secret_access_key")),
                secret=True,
            )
            session_token = _prompt_value(
                "AWS session token",
                default=_string_value(credentials.get("session_token")),
                secret=True,
                allow_empty=True,
            )
            with _console.status("Validating AWS credentials...", spinner="dots"):
                result = validate_aws_integration(
                    region=region,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    session_token=session_token,
                )
            _render_integration_result("AWS", result)
            if result.ok:
                upsert_integration(
                    "aws",
                    {
                        "credentials": {
                            "access_key_id": access_key_id,
                            "secret_access_key": secret_access_key,
                            "session_token": session_token,
                            "region": region,
                        }
                    },
                )
                env_path = sync_env_values(
                    {
                        "AWS_REGION": region,
                    }
                )
                return "AWS", str(env_path)

        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _github_wizard_browser_authorize() -> str | None:
    """Run GitHub device-flow browser authorization inside the wizard."""
    from rich.markup import escape

    from integrations.github_mcp_oauth import (
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


def _configure_notion() -> tuple[str, str]:
    _, credentials = _integration_defaults("notion")
    _console.print("\n[bold]Notion Integration[/bold]")
    _console.print("Create an internal integration at https://www.notion.so/my-integrations")
    _console.print("then share your target database with the integration.\n")

    while True:
        api_key = _prompt_value("Notion API key (secret_...)", secret=True)
        database_id = _prompt_value("Notion database ID")

        with _console.status("Validating Notion connection...", spinner="dots"):
            result = validate_notion_integration(api_key=api_key, database_id=database_id)
        _render_integration_result("Notion", result)

        if result.ok:
            upsert_integration(
                "notion", {"credentials": {"api_key": api_key, "database_id": database_id}}
            )
            env_path = sync_env_values({"NOTION_DATABASE_ID": database_id})
            return "Notion", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_jira() -> tuple[str, str]:
    _, credentials = _integration_defaults("jira")
    _console.print("\n[bold]Jira Integration[/bold]")
    _console.print(
        "Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens\n"
    )

    while True:
        base_url = _prompt_value("Jira base URL (e.g. https://myteam.atlassian.net)")
        email = _prompt_value("Jira account email")
        api_token = _prompt_value("Jira API token", secret=True)
        project_key = _prompt_value("Jira project key (e.g. OPS)")

        with _console.status("Validating Jira connection...", spinner="dots"):
            result = validate_jira_integration(
                base_url=base_url,
                email=email,
                api_token=api_token,
                project_key=project_key,
            )
        _render_integration_result("Jira", result)

        if result.ok:
            upsert_integration(
                "jira",
                {
                    "credentials": {
                        "base_url": base_url,
                        "email": email,
                        "api_token": api_token,
                        "project_key": project_key,
                    }
                },
            )
            env_path = sync_env_values({})
            return "Jira", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_google_docs() -> tuple[str, str]:
    _, credentials = _integration_defaults("google_docs")
    while True:
        credentials_file = _prompt_value(
            "Path to Google service account credentials JSON file",
            default=_string_value(credentials.get("credentials_file")),
        )
        folder_id = _prompt_value(
            "Google Drive folder ID for incident reports",
            default=_string_value(credentials.get("folder_id")),
        )
        with _console.status("Validating Google Docs integration...", spinner="dots"):
            result = validate_google_docs_integration(
                credentials_file=credentials_file,
                folder_id=folder_id,
            )
        _render_integration_result("Google Docs", result)
        if result.ok:
            upsert_integration(
                "google_docs",
                {
                    "credentials": {
                        "credentials_file": credentials_file,
                        "folder_id": folder_id,
                    }
                },
            )
            env_path = sync_env_values(
                {
                    "GOOGLE_CREDENTIALS_FILE": credentials_file,
                    "GOOGLE_DRIVE_FOLDER_ID": folder_id,
                }
            )
            return "Google Docs", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


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


def _configure_betterstack() -> tuple[str, str]:
    _, credentials = _integration_defaults("betterstack")
    while True:
        query_endpoint = _prompt_value(
            "Better Stack SQL query endpoint (e.g. https://eu-nbg-2-connect.betterstackdata.com)",
            default=_string_value(credentials.get("query_endpoint")),
        )
        username = _prompt_value(
            "Better Stack username (Integrations > Connect ClickHouse HTTP client)",
            default=_string_value(credentials.get("username")),
        )
        password = _prompt_value(
            "Better Stack password",
            default=_string_value(credentials.get("password")),
            secret=True,
        )
        sources_raw = _prompt_value(
            "Better Stack sources (comma-separated base IDs from dashboard, e.g. t123456_myapp; optional planner hint)",
            default=_joined_values(credentials.get("sources"), separator=",", fallback=""),
            allow_empty=True,
        )
        sources = [part.strip() for part in sources_raw.split(",") if part.strip()]

        with _console.status("Validating Better Stack integration...", spinner="dots"):
            result = validate_betterstack_integration(
                query_endpoint=query_endpoint,
                username=username,
                password=password,
                sources=sources,
            )
        _render_integration_result("Better Stack", result)
        if result.ok:
            upsert_integration(
                "betterstack",
                {
                    "credentials": {
                        "query_endpoint": query_endpoint,
                        "username": username,
                        "password": password,
                        "sources": sources,
                    }
                },
            )
            env_path = sync_env_values({})
            return "Better Stack", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_alertmanager() -> tuple[str, str]:
    _, credentials = _integration_defaults("alertmanager")
    while True:
        base_url = _prompt_value(
            "Alertmanager URL (e.g. http://alertmanager:9093)",
            default=_string_value(credentials.get("base_url")),
        )
        if not base_url:
            _console.print(f"[{ERROR}]Alertmanager URL is required.[/]")
            continue
        auth_choice = _choose(
            "Authentication method",
            [
                Choice(value="none", label="None (unauthenticated / internal network)"),
                Choice(value="bearer", label="Bearer token (reverse proxy auth)"),
                Choice(value="basic", label="Basic auth (username + password)"),
            ],
            default="none",
        )
        bearer_token = ""
        username = ""
        password = ""
        if auth_choice == "bearer":
            bearer_token = _prompt_value("Bearer token", secret=True)
        elif auth_choice == "basic":
            username = _prompt_value("Username")
            password = _prompt_value("Password", secret=True)
        with _console.status("Validating Alertmanager integration...", spinner="dots"):
            result = validate_alertmanager_integration(
                base_url=base_url,
                bearer_token=bearer_token,
                username=username,
                password=password,
            )
        _render_integration_result("Alertmanager", result)
        if result.ok:
            creds: dict[str, str] = {"base_url": base_url}
            if bearer_token:
                creds["bearer_token"] = bearer_token
            if username:
                creds["username"] = username
                creds["password"] = password
            upsert_integration("alertmanager", {"credentials": creds})
            env_path = sync_env_values({})
            return "Alertmanager", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_opsgenie() -> tuple[str, str]:
    _, credentials = _integration_defaults("opsgenie")
    while True:
        api_key = _prompt_value(
            "OpsGenie API key (Settings > API key management)",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        region = _prompt_value(
            "OpsGenie region (us or eu)",
            default=_string_value(credentials.get("region"), "us"),
        )
        with _console.status("Validating OpsGenie integration...", spinner="dots"):
            result = validate_opsgenie_integration(api_key=api_key, region=region)
        _render_integration_result("OpsGenie", result)
        if result.ok:
            upsert_integration(
                "opsgenie",
                {"credentials": {"api_key": api_key, "region": region}},
            )
            env_path = sync_env_values({})
            return "OpsGenie", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_pagerduty() -> tuple[str, str]:
    _, credentials = _integration_defaults("pagerduty")
    while True:
        api_key = _prompt_value(
            "PagerDuty API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        base_url = _prompt_value(
            "PagerDuty API base URL (press Enter to use default)",
            default=_string_value(credentials.get("base_url"), "https://api.pagerduty.com"),
        )
        with _console.status("Validating PagerDuty integration...", spinner="dots"):
            result = validate_pagerduty_integration(api_key=api_key, base_url=base_url)
        _render_integration_result("PagerDuty", result)
        if result.ok:
            upsert_integration(
                "pagerduty",
                {"credentials": {"api_key": api_key, "base_url": base_url}},
            )
            env_path = sync_env_values({})
            return "PagerDuty", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_incident_io() -> tuple[str, str]:
    _, credentials = _integration_defaults("incident_io")
    while True:
        api_key = _prompt_value(
            "incident.io API key",
            default=_string_value(credentials.get("api_key")),
            secret=True,
        )
        base_url = _prompt_value(
            "API base URL override (optional)",
            default=_string_value(credentials.get("base_url")),
            allow_empty=True,
        )
        with _console.status("Validating incident.io integration...", spinner="dots"):
            result = validate_incident_io_integration(
                api_key=api_key,
                base_url=base_url,
            )
        _render_integration_result("incident.io", result)
        if result.ok:
            credentials_payload = {
                "api_key": api_key,
                "base_url": base_url,
            }
            upsert_integration("incident_io", {"credentials": credentials_payload})
            sync_env_secret("INCIDENT_IO_API_KEY", api_key)
            env_path = sync_env_values(
                {
                    "INCIDENT_IO_BASE_URL": base_url,
                }
            )
            return "incident.io", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_discord() -> tuple[str, str]:
    _, credentials = _integration_defaults("discord")
    _console.print(
        "\n[bold]Discord Integration[/bold]\n"
        f"[{SECONDARY}]Get your credentials from https://discord.com/developers/applications.[/]\n"
    )
    while True:
        bot_token = _prompt_value(
            "Discord bot token",
            default=_string_value(credentials.get("bot_token")),
            secret=True,
        )
        application_id = _prompt_value(
            "Discord application ID",
            default=_string_value(credentials.get("application_id")),
        )
        public_key = _prompt_value(
            "Discord public key (from Developer Portal)",
            default=_string_value(credentials.get("public_key")),
        )
        default_channel_id = _prompt_value(
            "Default channel ID (optional)",
            default=_string_value(credentials.get("default_channel_id")),
            allow_empty=True,
        )
        with _console.status("Validating Discord bot token...", spinner="dots"):
            result = validate_discord_bot(bot_token=bot_token)
        _render_integration_result("Discord", result)
        if result.ok:
            upsert_integration(
                "discord",
                {
                    "credentials": {
                        "bot_token": bot_token,
                        "application_id": application_id,
                        "public_key": public_key,
                        "default_channel_id": default_channel_id,
                    }
                },
            )
            from integrations.cli import _register_discord_slash_command

            _register_discord_slash_command(application_id, bot_token)
            sync_env_secret("DISCORD_BOT_TOKEN", bot_token)
            env_path = sync_env_values(
                {
                    "DISCORD_APPLICATION_ID": application_id,
                    "DISCORD_PUBLIC_KEY": public_key,
                    "DISCORD_DEFAULT_CHANNEL_ID": default_channel_id,
                }
            )
            return "Discord", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_telegram() -> tuple[str, str]:
    _, credentials = _integration_defaults("telegram")
    _console.print(
        "\n[bold]Telegram Integration[/bold]\n"
        f"[{SECONDARY}]Create a bot with @BotFather, add it to your chat, then find "
        "chat_id via getUpdates. See docs/messaging/telegram for details.[/]\n"
    )
    while True:
        bot_token = _prompt_value(
            "Telegram bot token",
            default=_string_value(credentials.get("bot_token")),
            secret=True,
        )
        default_chat_id = _prompt_value(
            "Default chat ID (recommended for delivery)",
            default=_string_value(credentials.get("default_chat_id")),
            allow_empty=True,
        )
        with _console.status("Validating Telegram bot token...", spinner="dots"):
            result = validate_telegram_bot(bot_token=bot_token)
        _render_integration_result("Telegram", result)
        if result.ok:
            upsert_integration(
                "telegram",
                {
                    "credentials": {
                        "bot_token": bot_token,
                        "default_chat_id": default_chat_id or None,
                    }
                },
            )
            sync_env_secret("TELEGRAM_BOT_TOKEN", bot_token)
            env_values: dict[str, str] = {}
            if default_chat_id:
                env_values["TELEGRAM_DEFAULT_CHAT_ID"] = default_chat_id
            env_path = sync_env_values(env_values)
            if not default_chat_id:
                _console.print(
                    f"[{WARNING}]No default chat ID set — Hermes, watchdog, and scheduled "
                    "deliveries need TELEGRAM_DEFAULT_CHAT_ID to send messages.[/]"
                )
            return "Telegram", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_tempo() -> tuple[str, str]:
    _, credentials = _integration_defaults("tempo")
    _console.print(
        f"[{SECONDARY}]Tempo commonly runs without auth behind a gateway — a URL alone is enough.[/]"
    )
    _console.print(
        f"[{SECONDARY}]For auth, provide either a bearer token OR a username/password (not both).[/]"
    )
    while True:
        url = _prompt_value(
            "Tempo URL (e.g. http://localhost:3200)",
            default=_string_value(credentials.get("url")),
        )
        api_key = _prompt_value(
            "Tempo bearer token (optional, leave blank if using basic auth or none)",
            default=_string_value(credentials.get("api_key")),
            secret=True,
            allow_empty=True,
        )
        username = _prompt_value(
            "Tempo username (optional, for basic auth)",
            default=_string_value(credentials.get("username")),
            allow_empty=True,
        )
        password = _prompt_value(
            "Tempo password (optional, for basic auth)",
            default=_string_value(credentials.get("password")),
            secret=True,
            allow_empty=True,
        )
        org_id = _prompt_value(
            "Tempo tenant / X-Scope-OrgID (optional, leave blank if single-tenant)",
            default=_string_value(credentials.get("org_id")),
            allow_empty=True,
        )
        with _console.status("Validating Tempo integration...", spinner="dots"):
            result = validate_tempo_integration(
                url=url,
                api_key=api_key,
                username=username,
                password=password,
                org_id=org_id,
            )
        _render_integration_result("Tempo", result)
        if result.ok:
            creds: dict[str, str] = {"url": url}
            if api_key:
                creds["api_key"] = api_key
            if username:
                creds["username"] = username
            if password:
                creds["password"] = password
            if org_id:
                creds["org_id"] = org_id
            upsert_integration("tempo", {"credentials": creds})
            env_values: dict[str, str] = {"TEMPO_URL": url}
            if api_key:
                env_values["TEMPO_API_KEY"] = api_key
            if username:
                env_values["TEMPO_USERNAME"] = username
            if password:
                env_values["TEMPO_PASSWORD"] = password
            if org_id:
                env_values["TEMPO_ORG_ID"] = org_id
            env_path = sync_env_values(env_values)
            return "Tempo", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_splunk() -> tuple[str, str]:
    _, credentials = _integration_defaults("splunk")
    while True:
        base_url = _prompt_value(
            "Splunk REST API base URL (e.g. https://splunk.corp.com:8089)",
            default=_string_value(credentials.get("base_url")),
        )
        token = _prompt_value(
            "Splunk API bearer token",
            default=_string_value(credentials.get("token")),
            secret=True,
        )
        index = _prompt_value(
            "Default Splunk index to search",
            default=_string_value(credentials.get("index"), "main"),
        )
        verify_ssl = _confirm(
            "Verify SSL certificate?",
            default=bool(credentials.get("verify_ssl", True)),
        )
        ca_bundle = ""
        if verify_ssl:
            ca_bundle = _prompt_value(
                "Path to CA bundle for SSL verification (leave empty to use system defaults)",
                default=_string_value(credentials.get("ca_bundle")),
                allow_empty=True,
            )
        with _console.status("Validating Splunk integration...", spinner="dots"):
            result = validate_splunk_integration(
                base_url=base_url,
                token=token,
                index=index,
                verify_ssl=verify_ssl,
                ca_bundle=ca_bundle,
            )
        _render_integration_result("Splunk", result)
        if result.ok:
            upsert_integration(
                "splunk",
                {
                    "credentials": {
                        "base_url": base_url,
                        "token": token,
                        "index": index,
                        "verify_ssl": verify_ssl,
                        "ca_bundle": ca_bundle,
                    }
                },
            )
            env_values: dict[str, str] = {
                "SPLUNK_URL": base_url,
                "SPLUNK_INDEX": index,
                "SPLUNK_VERIFY_SSL": "true" if verify_ssl else "false",
                # Do NOT write SPLUNK_TOKEN to .env — it goes to the credential store only
            }
            if ca_bundle:
                env_values["SPLUNK_CA_BUNDLE"] = ca_bundle
            env_path = sync_env_values(env_values)
            return "Splunk", str(env_path)
        _console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_opensearch() -> tuple[str, str]:
    _, credentials = _integration_defaults("opensearch")
    while True:
        url = _prompt_value(
            "OpenSearch URL (e.g. https://my-cluster.us-east-1.es.amazonaws.com)",
            default=_string_value(credentials.get("url")),
        )
        auth_choice = _choose(
            "Authentication method",
            [
                Choice(
                    value="basic",
                    label="Username + Password (HTTP Basic Auth)",
                    hint="Default for self-hosted OpenSearch",
                ),
                Choice(
                    value="api_key",
                    label="API key",
                    hint="Native to Elasticsearch and some OpenSearch deployments",
                ),
                Choice(
                    value="none",
                    label="None (security disabled)",
                    hint="Clusters without authentication enabled",
                ),
            ],
            default="basic",
        )
        api_key = ""
        username = ""
        password = ""
        if auth_choice == "api_key":
            api_key = _prompt_value(
                "OpenSearch API key",
                default=_string_value(credentials.get("api_key")),
                secret=True,
            )
            # Guard against empty api_key reaching the cluster probe.
            # On a cluster with security disabled the probe would return 200,
            # silently dropping the user's chosen auth method and persisting
            # the integration as URL-only.
            if not api_key:
                _console.print(
                    f"[{ERROR}]  {GLYPH_ERROR}  API key is required when using API key authentication.[/]"
                )
                continue
        elif auth_choice == "basic":
            username = _prompt_value(
                "OpenSearch username",
                default=_string_value(credentials.get("username"), "admin"),
            )
            password = _prompt_value(
                "OpenSearch password",
                default=_string_value(credentials.get("password")),
                secret=True,
            )
            # Guard against half-populated Basic Auth credentials reaching the
            # cluster probe. ElasticsearchConfig.headers silently drops the
            # Authorization header when either half is empty, so the agent
            # would send unauthenticated requests against a security-enabled
            # cluster and fail with a confusing 401.
            if not username or not password:
                _console.print(
                    f"[{ERROR}]  {GLYPH_ERROR}  Both username and password are required for Basic Auth.[/]"
                )
                continue
        with _console.status("Validating OpenSearch integration...", spinner="dots"):
            result = validate_opensearch_integration(
                url=url,
                api_key=api_key,
                username=username,
                password=password,
            )
        _render_integration_result("OpenSearch", result)
        if result.ok:
            creds: dict[str, str] = {"url": url}
            if api_key:
                creds["api_key"] = api_key
            if username:
                creds["username"] = username
                creds["password"] = password
            upsert_integration("opensearch", {"credentials": creds})
            env_values: dict[str, str] = {
                "OPENSEARCH_URL": url,
            }
            if api_key:
                sync_env_secret("OPENSEARCH_API_KEY", api_key)
            if username:
                env_values["OPENSEARCH_USERNAME"] = username
                sync_env_secret("OPENSEARCH_PASSWORD", password)
            env_path = sync_env_values(env_values)
            return "OpenSearch", str(env_path)
        _console.print(f"[{DIM}]Try again or press Ctrl+C to cancel.[/]")


def _configure_selected_integrations() -> tuple[list[str], str | None]:
    configured: list[str] = []
    last_env_path: str | None = None

    _console.print(
        f"[{SECONDARY}]Pick one integration to wire up now, or skip this step and come back later.[/]"
    )
    integration_choices = list(ONBOARD_INTEGRATION_CHOICES)
    selected_service = _choose(
        "Choose an integration to configure",
        integration_choices,
        default="grafana_local",
        group_order=ONBOARD_INTEGRATION_GROUP_ORDER,
        trailing_choices=[ONBOARD_SKIP_CHOICE],
    )
    if selected_service == "skip":
        return configured, last_env_path

    handlers = {
        "grafana_local": _configure_grafana_local,
        "grafana": _configure_grafana,
        "datadog": _configure_datadog,
        "honeycomb": _configure_honeycomb,
        "coralogix": _configure_coralogix,
        "slack": _configure_slack,
        "discord": _configure_discord,
        "telegram": _configure_telegram,
        "aws": _configure_aws,
        "github": _configure_github_mcp,
        "sentry": _configure_sentry,
        "gitlab": _configure_gitlab,
        "jenkins": _configure_jenkins,
        "google_docs": _configure_google_docs,
        "vercel": _configure_vercel,
        "dagster": _configure_dagster,
        "betterstack": _configure_betterstack,
        "jira": _configure_jira,
        "alertmanager": _configure_alertmanager,
        "opsgenie": _configure_opsgenie,
        "pagerduty": _configure_pagerduty,
        "incident_io": _configure_incident_io,
        "notion": _configure_notion,
        "openclaw": _configure_openclaw,
        "posthog_mcp": _configure_posthog_mcp,
        "sentry_mcp": _configure_sentry_mcp,
        "opensearch": _configure_opensearch,
        "splunk": _configure_splunk,
        "tempo": _configure_tempo,
    }
    _SERVICE_LABELS = {
        "grafana_local": "grafana local",
        "grafana": "grafana",
        "datadog": "datadog",
        "honeycomb": "honeycomb",
        "coralogix": "coralogix",
        "slack": "slack",
        "discord": "discord",
        "telegram": "telegram",
        "aws": "aws",
        "github": "github mcp",
        "sentry": "sentry",
        "gitlab": "gitlab",
        "jenkins": "jenkins",
        "google_docs": "google docs",
        "vercel": "vercel",
        "dagster": "dagster",
        "jira": "jira",
        "alertmanager": "alertmanager",
        "opsgenie": "opsgenie",
        "pagerduty": "pagerduty",
        "incident_io": "incident.io",
        "notion": "notion",
        "openclaw": "openclaw",
        "posthog_mcp": "posthog mcp",
        "sentry_mcp": "sentry mcp",
        "opensearch": "opensearch",
        "tempo": "grafana tempo",
    }

    _step(f"Service · {_SERVICE_LABELS.get(selected_service, selected_service)}")
    if selected_service == "vercel":
        _console.print(
            f"[{SECONDARY}]Note: Vercel's runtime-log API may omit or delay lines compared to the "
            "dashboard. Deployment and build checks still apply; there is no CLI incident browser.[/]"
        )
    try:
        label, env_path = handlers[selected_service]()
        configured.append(label)
        last_env_path = env_path
    except KeyboardInterrupt:
        _console.print(
            f"[{WARNING}]{_SERVICE_LABELS.get(selected_service, selected_service)} setup skipped.[/]"
        )

    return configured, last_env_path
