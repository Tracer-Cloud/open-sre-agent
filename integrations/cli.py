"""Interactive CLI for managing local integrations (~/.opensre/integrations.json).

Usage:
    python -m integrations setup <service>
    python -m integrations list
    python -m integrations show <service>
    python -m integrations remove <service>
    python -m integrations verify [service] [--send-slack-test]
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, NoReturn, cast

import questionary

from platform.common.url_validation import validate_https_or_loopback_http_url
from platform.terminal.prompt_support import (
    QUESTIONARY_QMARK,
    questionary_prompt_style,
)
from platform.terminal.theme import (
    ANSI_BOLD,
    ANSI_DIM,
    ANSI_RESET,
    DEVICE_CODE_ANSI,
)

if TYPE_CHECKING:
    from integrations.github.mcp import GitHubMcpDisplayDetailLevel
    from integrations.setup_flow import IntegrationSetupSpec

from integrations.openclaw import build_openclaw_config, validate_openclaw_config
from integrations.posthog_mcp import (
    DEFAULT_POSTHOG_MCP_URL,
    build_posthog_mcp_config,
    validate_posthog_mcp_config,
)
from integrations.registry import SUPPORTED_SETUP_SERVICES, resolve_management_service
from integrations.sentry_mcp import (
    DEFAULT_SENTRY_MCP_URL,
    build_sentry_mcp_config,
    validate_sentry_mcp_config,
)
from integrations.store import (
    STORE_PATH,
    get_integration,
    list_integrations,
    remove_integration,
    upsert_integration,
)
from integrations.verify import (
    SUPPORTED_VERIFY_SERVICES,
    format_verification_results,
    verification_exit_code,
    verify_integrations,
)
from integrations.x_mcp import (
    DEFAULT_X_MCP_URL,
    build_x_mcp_config,
    validate_x_mcp_config,
)

_B = ANSI_BOLD
_R = ANSI_RESET
_DIM = ANSI_DIM


def _json_echo(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


_SECRET_KEYS = frozenset(
    {
        "api_token",
        "api_key",
        "api_private_key",
        "app_key",
        "bearer_token",
        "bot_token",
        "password",
        "secret_access_key",
        "session_token",
        "jwt_token",
        "webhook_url",
        "auth_token",
        "connection_string",
    }
)


def _select(message: str, choices: list[Any], **kwargs: Any) -> Any:
    return questionary.select(
        message,
        choices=choices,
        qmark=QUESTIONARY_QMARK,
        style=questionary_prompt_style(),
        **kwargs,
    ).ask()


def _confirm(message: str, **kwargs: Any) -> Any:
    return questionary.confirm(
        message, qmark=QUESTIONARY_QMARK, style=questionary_prompt_style(), **kwargs
    ).ask()


def _p(label: str, default: str = "", secret: bool = False) -> str:
    try:
        if secret:
            result = questionary.password(
                label, qmark=QUESTIONARY_QMARK, style=questionary_prompt_style()
            ).ask()
        else:
            result = questionary.text(
                label, default=default, qmark=QUESTIONARY_QMARK, style=questionary_prompt_style()
            ).ask()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    if result is None:
        print("\nAborted.")
        sys.exit(1)
    return result.strip() or default


def _die(msg: str) -> NoReturn:
    print(f"  error: {msg}", file=sys.stderr)
    sys.exit(1)


def _prompt_github_repo_report_level() -> GitHubMcpDisplayDetailLevel:
    """Ask how much repository access detail to print after a successful validation."""

    try:
        sel = _select(
            "How much repository detail should we show?",
            choices=[
                questionary.Choice("Brief (recommended) — no repo names", value="summary"),
                questionary.Choice("Standard — scope summary only", value="standard"),
                questionary.Choice("Expanded — include repo names", value="full"),
            ],
            default="summary",
        )
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    if sel is None:
        return "summary"
    if sel in ("summary", "standard", "full"):
        from integrations.github.mcp import GitHubMcpDisplayDetailLevel as _Detail

        return cast(_Detail, sel)
    return "summary"


def _parse_port(raw: str, default: int = 3306) -> int:
    """Parse a port string, returning *default* for invalid or out-of-range values."""
    try:
        port = int(raw)
    except (ValueError, TypeError):
        return default
    if port < 1 or port > 65535:
        return default
    return port


def _mask(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: (v[:4] + "****" if isinstance(v, str) and v else "****")
            if k in _SECRET_KEYS
            else _mask(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask(i) for i in obj]
    return obj


# ─── setup flows ──────────────────────────────────────────────────────────────


def _setup_grafana() -> None:
    endpoint = _p("Instance URL (e.g. https://myorg.grafana.net)")
    api_key = _p("Service account token", secret=True)
    if not endpoint or not api_key:
        _die("endpoint and api_key are required.")
    upsert_integration("grafana", {"credentials": {"endpoint": endpoint, "api_key": api_key}})


def _setup_datadog() -> None:
    from integrations.datadog.setup import DATADOG_SETUP

    _run_spec_setup(DATADOG_SETUP)


def _setup_groundcover() -> None:
    from integrations.groundcover.setup import GROUNDCOVER_SETUP

    _run_spec_setup(GROUNDCOVER_SETUP)


def _setup_honeycomb() -> None:
    from integrations.honeycomb.setup import HONEYCOMB_SETUP

    _run_spec_setup(HONEYCOMB_SETUP)


def _setup_coralogix() -> None:
    from integrations.coralogix.setup import CORALOGIX_SETUP

    _run_spec_setup(CORALOGIX_SETUP)


def _setup_aws() -> None:
    choice = _select(
        "AWS authentication method:",
        choices=[
            questionary.Choice("IAM Role ARN", value="1"),
            questionary.Choice("Access Key + Secret", value="2"),
        ],
        instruction="(use arrow keys)",
    )
    if choice is None:
        print("\nAborted.")
        sys.exit(1)
    region = _p("Region", default="us-east-1")
    if choice == "1":
        role_arn = _p("IAM Role ARN")
        if not role_arn:
            _die("role_arn is required.")
        upsert_integration(
            "aws",
            {
                "role_arn": role_arn,
                "external_id": _p("External ID (optional)"),
                "credentials": {"region": region},
            },
        )
    else:
        access_key = _p("AWS_ACCESS_KEY_ID", secret=True)
        secret_key = _p("AWS_SECRET_ACCESS_KEY", secret=True)
        if not access_key or not secret_key:
            _die("access_key and secret_key are required.")
        upsert_integration(
            "aws",
            {
                "credentials": {
                    "access_key_id": access_key,
                    "secret_access_key": secret_key,
                    "session_token": _p("Session token (optional)"),
                    "region": region,
                }
            },
        )


def _setup_slack() -> None:
    """Configure Slack delivery webhook and/or Socket Mode gateway tokens.

    Mirrors Telegram setup: credentials land in the integration store so
    ``load_slack_gateway_settings`` and outbound delivery can read them.
    Existing credentials are merged so re-running setup does not wipe the
    other mode.
    """
    from integrations.store import get_integration

    existing = get_integration("slack") or {}
    creds = dict(existing.get("credentials") or {})

    mode = _select(
        "Slack setup:",
        choices=[
            questionary.Choice("Incoming webhook (outbound delivery)", value="webhook"),
            questionary.Choice("Socket Mode bot (two-way gateway chat)", value="socket"),
            questionary.Choice("Both webhook and Socket Mode", value="both"),
        ],
        instruction="(use arrow keys)",
    )
    if mode is None:
        print("\nAborted.")
        sys.exit(1)

    if mode in {"webhook", "both"}:
        webhook_url = _p(
            "Slack webhook URL",
            secret=True,
            default=str(creds.get("webhook_url") or ""),
        )
        if not webhook_url:
            _die("webhook_url is required for webhook setup.")
        creds["webhook_url"] = webhook_url

    if mode in {"socket", "both"}:
        bot_token = _p(
            "Slack bot token (xoxb-…)",
            secret=True,
            default=str(creds.get("bot_token") or ""),
        )
        app_token = _p(
            "Slack app-level token (xapp-…)",
            secret=True,
            default=str(creds.get("app_token") or ""),
        )
        if not bot_token or not app_token:
            _die("bot_token and app_token are required for Socket Mode setup.")
        if not bot_token.startswith("xoxb-"):
            _die("bot_token must start with xoxb-")
        if not app_token.startswith("xapp-"):
            _die("app_token must start with xapp-")
        creds["bot_token"] = bot_token
        creds["app_token"] = app_token
        print("\n  Next for the gateway:")
        print("    - opensre messaging allow -p slack -u <U…>")
        print("    - opensre gateway start")

    upsert_integration("slack", {"credentials": creds})


def _setup_opensearch() -> None:
    url = _p("URL (e.g. https://my-cluster.us-east-1.es.amazonaws.com)")
    if not url:
        _die("url is required.")
    creds: dict[str, Any] = {"url": url}
    auth_choice = _select(
        "OpenSearch authentication method:",
        choices=[
            questionary.Choice("Username + Password (HTTP Basic Auth)", value="basic"),
            questionary.Choice("API key", value="api_key"),
            questionary.Choice("None (security disabled)", value="none"),
        ],
        instruction="(use arrow keys)",
    )
    if auth_choice is None:
        print("\nAborted.")
        sys.exit(1)
    if auth_choice == "api_key":
        api_key = _p("API key", secret=True)
        if not api_key:
            _die("api_key is required.")
        creds["api_key"] = api_key
    elif auth_choice == "basic":
        username = _p("Username", default="admin")
        password = _p("Password", secret=True)
        if not username or not password:
            _die("username and password are required for basic auth.")
        creds["username"] = username
        creds["password"] = password
    upsert_integration("opensearch", {"credentials": creds})


def _setup_servicenow() -> None:
    instance_url = _p("Instance URL (e.g. https://dev12345.service-now.com)")
    if not instance_url:
        _die("instance_url is required.")
    # Fail here with an actionable message instead of storing a URL that
    # classification would later reject silently (verify would say "missing").
    try:
        instance_url = validate_https_or_loopback_http_url(
            instance_url.strip().rstrip("/"),
            service_name="servicenow",
            field_name="instance_url",
        )
    except ValueError as exc:
        _die(str(exc))
    username = _p("Username")
    password = _p("Password", secret=True)
    if not username or not password:
        _die("username and password are required.")
    upsert_integration(
        "servicenow",
        {
            "credentials": {
                "instance_url": instance_url,
                "username": username,
                "password": password,
            }
        },
    )


def _setup_rds() -> None:
    host = _p("Host (e.g. mydb.xxxx.us-east-1.rds.amazonaws.com)")
    port = _p("Port", default="5432")
    database = _p("Database name")
    username = _p("Username")
    password = _p("Password", secret=True)
    if not host or not database or not username:
        _die("host, database, and username are required.")
    upsert_integration(
        "rds",
        {
            "credentials": {
                "host": host,
                "port": int(port) if port.isdigit() else 5432,
                "database": database,
                "username": username,
                "password": password,
            }
        },
    )


def _setup_tracer() -> None:
    from integrations.tracer.setup import TRACER_SETUP

    _run_spec_setup(TRACER_SETUP)


def _setup_vercel() -> None:
    from integrations.vercel.setup import VERCEL_SETUP

    _run_spec_setup(VERCEL_SETUP)


def _setup_betterstack() -> None:
    query_endpoint = _p(
        "Better Stack SQL query endpoint (e.g. https://eu-nbg-2-connect.betterstackdata.com)"
    )
    username = _p("Better Stack username (Integrations > Connect ClickHouse HTTP client)")
    password = _p("Better Stack password", secret=True)
    sources_raw = _p(
        "Better Stack sources, comma-separated base IDs from dashboard (optional hint for the planner)"
    )
    if not query_endpoint or not username:
        _die("query_endpoint and username are required.")
    sources = [part.strip() for part in (sources_raw or "").split(",") if part.strip()]
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


def _setup_incident_io() -> None:
    from integrations.incident_io.setup import INCIDENT_IO_SETUP

    _run_spec_setup(INCIDENT_IO_SETUP)


def _github_browser_authorize() -> str | None:
    """Run GitHub device-flow browser authorization.

    Returns the access token, or ``None`` when the flow is unavailable so the
    caller can fall back to manual token entry.
    """
    from integrations.github.mcp_oauth import (
        GitHubDeviceCode,
        GitHubDeviceFlowError,
        authorize_github_via_device_flow,
    )

    def _show(code: GitHubDeviceCode) -> None:
        print()
        print(f"  1. Your browser will open {code.verification_uri}")
        print("     (if it doesn't open automatically, visit that URL yourself).")
        print(
            f"  2. Enter this one-time code when GitHub asks: {DEVICE_CODE_ANSI}{code.user_code}{_R}"
        )
        print("  3. Approve the request for OpenSRE.")
        print()
        print(f"  {_DIM}Waiting for you to approve in the browser… (Ctrl-C to cancel){_R}")

    print()
    print("  Sign in to GitHub in your browser (device authorization):")
    print(f"  {_DIM}Requesting a one-time code from GitHub…{_R}")
    try:
        token = authorize_github_via_device_flow(on_prompt=_show)
    except GitHubDeviceFlowError as err:
        print(f"  Browser authorization unavailable: {err}", file=sys.stderr)
        return None
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    except Exception as err:  # network/transport issues
        print(f"  Browser authorization failed: {err}", file=sys.stderr)
        return None
    print(f"  {_B}Authorized.{_R} Saved a GitHub token from the browser sign-in.")
    return token.access_token


def _setup_github_auth_token(mode: str) -> str:
    """Resolve a GitHub MCP auth token, offering browser sign-in for remote modes."""
    if mode == "stdio":
        return _p(
            "GitHub PAT / auth token (optional if the server authenticates upstream)",
            secret=True,
        )

    auth_method = _select(
        "How do you want to connect OpenSRE to GitHub?",
        choices=[
            questionary.Choice(
                "Sign in with GitHub in your browser (opens a page, enter a one-time code)",
                value="browser",
            ),
            questionary.Choice("Paste a personal access token (PAT)", value="token"),
            questionary.Choice("Skip — the MCP server authenticates upstream", value="none"),
        ],
        default="browser",
    )
    if auth_method is None:
        print("\nAborted.")
        sys.exit(1)
    if auth_method == "none":
        return ""
    if auth_method == "browser":
        token = _github_browser_authorize()
        if token:
            return token
        print("  Falling back to manual token entry.")
    return _p("GitHub PAT / auth token", secret=True)


def _github_advanced_setup(credentials: dict[str, Any]) -> tuple[str, str]:
    """Prompt the advanced GitHub MCP knobs and return (repo_view, repo_visibility).

    Mutates ``credentials`` in place with mode/url/command/args/auth_token/toolsets.
    """
    from integrations.github.mcp import (
        DEFAULT_GITHUB_MCP_TOOLSETS,
        DEFAULT_GITHUB_MCP_URL,
    )

    # Transport is fixed to Streamable HTTP. In practice it is the only mode anyone
    # selects, and SSE/stdio are deprecated for the hosted GitHub MCP server. The
    # transport prompt was removed on purpose — do NOT reintroduce a transport
    # selection or a stdio branch here.
    mode = "streamable-http"
    credentials["mode"] = mode
    url = _p("MCP URL", default=DEFAULT_GITHUB_MCP_URL)
    if not url:
        _die("url is required for remote MCP modes.")
    credentials["url"] = url
    credentials["auth_token"] = _setup_github_auth_token(mode)
    toolsets = _p("Toolsets", default=",".join(DEFAULT_GITHUB_MCP_TOOLSETS))
    credentials["toolsets"] = [part.strip() for part in toolsets.split(",") if part.strip()]

    repo_view = _select(
        "Which repository view should we use to verify access?",
        choices=[
            questionary.Choice("Auto (recommended)", value="auto"),
            questionary.Choice("Your repositories", value="user"),
            questionary.Choice("Accessible repositories", value="accessible"),
            questionary.Choice("Starred repositories", value="starred"),
            questionary.Choice("Search: user:<your_login>", value="search_user"),
        ],
        default="auto",
    )
    if repo_view is None:
        print("\nAborted.")
        sys.exit(1)
    repo_visibility = _select(
        "Filter repositories by visibility (best-effort)",
        choices=[
            questionary.Choice("Any (recommended)", value="any"),
            questionary.Choice("Public only", value="public"),
            questionary.Choice("Private only", value="private"),
        ],
        default="any",
    )
    if repo_visibility is None:
        print("\nAborted.")
        sys.exit(1)
    return repo_view, repo_visibility


def _setup_github() -> str | None:
    """Configure + validate + save the GitHub MCP integration.

    Returns the authenticated GitHub login on success (``None`` if the validated
    result carried no login), so callers like the first-launch gate can propagate
    the username. Exits the process on validation failure.
    """
    from integrations.github.mcp import (
        DEFAULT_GITHUB_MCP_MODE,
        DEFAULT_GITHUB_MCP_TOOLSETS,
        DEFAULT_GITHUB_MCP_URL,
        GitHubMcpDisplayDetailLevel,
        GitHubMcpRepoView,
        GitHubMcpRepoVisibilityFilter,
        build_github_mcp_config,
        format_github_mcp_validation_cli_report,
        print_github_mcp_validation_report,
        validate_github_mcp_config,
    )

    print("  Connect OpenSRE to GitHub through the hosted GitHub MCP server.")
    advanced = _confirm(
        "Customize advanced settings (transport, server URL, toolsets, repo scope)?",
        default=False,
    )
    if advanced is None:
        print("\nAborted.")
        sys.exit(1)

    credentials: dict[str, Any] = {}
    repo_view: str = "auto"
    repo_visibility: str = "any"

    if advanced:
        repo_view, repo_visibility = _github_advanced_setup(credentials)
    else:
        credentials["mode"] = DEFAULT_GITHUB_MCP_MODE
        credentials["url"] = DEFAULT_GITHUB_MCP_URL
        credentials["auth_token"] = _setup_github_auth_token(DEFAULT_GITHUB_MCP_MODE)
        credentials["toolsets"] = list(DEFAULT_GITHUB_MCP_TOOLSETS)

    print("\n  Validating GitHub MCP integration...")
    mcp_config = build_github_mcp_config(credentials)
    result = validate_github_mcp_config(
        mcp_config,
        repo_view=cast(GitHubMcpRepoView, repo_view),
        repo_visibility=cast(GitHubMcpRepoVisibilityFilter, repo_visibility),
    )
    if result.ok:
        # The simple path stays concise: identity + tool availability, no repo dump.
        # Only the advanced path offers the verbose repo listing.
        level = (
            _prompt_github_repo_report_level()
            if advanced
            else cast(GitHubMcpDisplayDetailLevel, "summary")
        )
        print()
        print_github_mcp_validation_report(result, detail_level=level)
    else:
        for line in format_github_mcp_validation_cli_report(result).splitlines():
            print(f"  {line}")
        sys.exit(1)

    if result.authenticated_user:
        # Persist the resolved GitHub login as a non-secret credential field so
        # surfaces like the welcome banner can greet the user by their GitHub
        # handle instead of the local system username.
        credentials["username"] = result.authenticated_user
    upsert_integration("github", {"credentials": credentials})
    if result.authenticated_user:
        from platform.analytics.cli import identify_github_username

        identify_github_username(result.authenticated_user)
    return result.authenticated_user


def _setup_gitlab() -> None:
    from integrations.gitlab.setup import GITLAB_SETUP

    _run_spec_setup(GITLAB_SETUP)


def _setup_sentry() -> None:
    from integrations.sentry.setup import SENTRY_SETUP

    _run_spec_setup(SENTRY_SETUP)


def _setup_posthog() -> None:
    from integrations.posthog.setup import POSTHOG_SETUP

    _run_spec_setup(POSTHOG_SETUP)


def _setup_mongodb() -> None:
    connection_string = _p(
        "Connection string (e.g. mongodb+srv://user:pass@cluster.example.net)", secret=True
    )
    database = _p("Database name")
    auth_source = _p("Auth source", default="admin")
    tls_choice = _select(
        "TLS enabled?",
        choices=[
            questionary.Choice("Yes", value="1"),
            questionary.Choice("No", value="0"),
        ],
        instruction="(use arrow keys)",
    )
    if tls_choice is None:
        print("\nAborted.")
        sys.exit(1)
    tls = tls_choice == "1"
    if not connection_string:
        _die("connection_string is required.")
    upsert_integration(
        "mongodb",
        {
            "credentials": {
                "connection_string": connection_string,
                "database": database,
                "auth_source": auth_source,
                "tls": tls,
            }
        },
    )


def _setup_redis() -> None:
    host = _p("Host (e.g. localhost or redis.example.net)")
    if not host:
        _die("host is required.")
    port_input = _p("Port", default="6379")
    username = _p("Username (leave blank unless using Redis ACLs)")
    password = _p("Password (leave blank if not set)", secret=True)
    db_input = _p("Database number", default="0")
    ssl_choice = _select(
        "Use TLS?",
        choices=[
            questionary.Choice("No", value="0"),
            questionary.Choice("Yes", value="1"),
        ],
        instruction="(use arrow keys)",
    )
    if ssl_choice is None:
        print("\nAborted.")
        sys.exit(1)
    ssl = ssl_choice == "1"
    try:
        port = int(port_input)
    except (TypeError, ValueError):
        _die(f"port: {port_input} is invalid")
    try:
        db = int(db_input)
    except (TypeError, ValueError):
        _die(f"db: {db_input} is invalid")
    upsert_integration(
        "redis",
        {
            "credentials": {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "db": db,
                "ssl": ssl,
            }
        },
    )


def _register_discord_slash_command(application_id: str, bot_token: str) -> None:
    import httpx

    url = f"https://discord.com/api/v10/applications/{application_id}/commands"
    payload = {
        "name": "investigate",
        "description": "Trigger an OpenSRE investigation",
        "options": [
            {
                "name": "alert",
                "description": "Alert JSON or description",
                "type": 3,
                "required": True,
            }
        ],
    }
    resp = httpx.put(url, json=[payload], headers={"Authorization": f"Bot {bot_token}"}, timeout=10)
    if resp.is_success:
        print("  ✓ /investigate slash command registered.")
    else:
        print(f"  ⚠ Slash command registration failed ({resp.status_code}): {resp.text}")


def _setup_discord() -> None:
    bot_token = _p("Discord bot token", secret=True)
    application_id = _p("Discord application ID")
    public_key = _p("Discord public key (from Developer Portal)")
    default_channel_id = _p("Default channel ID (optional)")
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
    _register_discord_slash_command(application_id, bot_token)


def _run_spec_setup(spec: IntegrationSetupSpec) -> None:
    """Prompt for a spec's fields, then validate, verify, and persist them.

    Each field is checked as it is answered so a blank required value fails
    immediately, rather than after the user has worked through the rest of the
    prompts.
    """
    from integrations.setup_flow import apply_setup

    values: dict[str, str | None] = {}
    for field in spec.fields:
        value = _p(field.question, default=field.default, secret=field.secret)
        # A field with a default is never missing — apply_setup substitutes it —
        # so only a defaultless required field can fail here.
        if not value and field.required and not field.default:
            _die(f"{field.label} is required.")
        values[field.name] = value

    print(f"\n  Validating {spec.service} credentials...")
    outcome = apply_setup(spec, values)
    if not outcome.ok:
        _die(outcome.detail)
    print(f"  {outcome.detail}")
    print("  Next:")
    print(f"    - opensre integrations verify {spec.service}")


def _setup_telegram() -> None:
    from integrations.telegram.setup import TELEGRAM_SETUP

    _run_spec_setup(TELEGRAM_SETUP)


def _setup_rocketchat() -> None:
    from integrations.rocketchat.verifier import verify_rocketchat

    server_url = _p("Rocket.Chat server URL (e.g. https://chat.example.com)")
    auth_token = _p("Rocket.Chat personal access token (blank for webhook-only)", secret=True)
    user_id = _p("Rocket.Chat user ID (blank for webhook-only)")
    webhook_url = _p("Rocket.Chat incoming webhook URL (optional)", secret=True)
    has_pat = bool(server_url and auth_token and user_id)
    if not has_pat and not webhook_url:
        _die("Provide either a webhook_url or all of server_url, auth_token, and user_id.")
    default_channel = _p("Default channel (e.g. #incidents, optional)")
    print("\n  Validating Rocket.Chat credentials...")
    result = verify_rocketchat(
        "setup",
        {
            "server_url": server_url,
            "auth_token": auth_token,
            "user_id": user_id,
            "webhook_url": webhook_url,
        },
    )
    if result["status"] != "passed":
        _die(result["detail"])
    print(f"  {result['detail']}")
    upsert_integration(
        "rocketchat",
        {
            "credentials": {
                "server_url": server_url,
                "auth_token": auth_token,
                "user_id": user_id,
                "webhook_url": webhook_url,
                "default_channel": default_channel or None,
            }
        },
    )
    print("  Next:")
    print("    - opensre integrations verify rocketchat")


def _setup_smtp() -> None:
    from integrations.smtp.setup import SMTP_SETUP

    _run_spec_setup(SMTP_SETUP)


def _setup_whatsapp() -> None:
    from integrations.whatsapp.setup import WHATSAPP_SETUP

    _run_spec_setup(WHATSAPP_SETUP)


def _setup_twilio() -> None:
    """Wizard for the Twilio SMS integration.

    WhatsApp delivery is configured separately via ``setup whatsapp``.
    """
    account_sid = _p("Twilio Account SID (starts with AC...)")
    auth_token = _p("Twilio Auth Token", secret=True)
    if not account_sid or not auth_token:
        _die("account_sid and auth_token are required.")

    sms_from = _p(
        "Twilio SMS From number (E.164, e.g. +14155551234; leave blank to use a Messaging Service SID)"
    )
    messaging_service_sid = ""
    if not sms_from:
        messaging_service_sid = _p("Twilio Messaging Service SID (starts with MG...)")
        if not messaging_service_sid:
            _die("SMS requires either a from_number or a messaging_service_sid.")

    upsert_integration(
        "twilio",
        {
            "credentials": {
                "account_sid": account_sid,
                "auth_token": auth_token,
                "sms": {
                    "enabled": True,
                    "from_number": sms_from,
                    "messaging_service_sid": messaging_service_sid,
                    "default_to": _p("Default SMS recipient (optional, E.164)") or None,
                },
            }
        },
    )


def _setup_openclaw() -> None:
    # Transport is fixed to stdio (the local OpenClaw bridge). In practice it is the
    # only mode anyone selects, so the transport prompt was removed on purpose — do
    # NOT reintroduce a transport selection or a remote streamable-http/SSE branch.
    mode = "stdio"
    credentials: dict[str, Any] = {"mode": mode}
    command = _p("OpenClaw bridge command", default="openclaw")
    args = _p("OpenClaw bridge args", default="mcp serve")
    if not command:
        _die("command is required for stdio mode.")
    credentials["command"] = command
    credentials["args"] = [part for part in args.split() if part]
    credentials["url"] = ""
    credentials["auth_token"] = ""

    print("\n  Validating OpenClaw bridge...")
    config = build_openclaw_config(credentials)
    result = validate_openclaw_config(config)
    print(f"  {result.detail}")
    if not result.ok:
        sys.exit(1)

    upsert_integration("openclaw", {"credentials": credentials})
    print("  Next:")
    print("    - opensre integrations verify openclaw")
    print("    - uv run opensre investigate -i tests/fixtures/openclaw_test_alert.json")
    print("    - for accurate RCA, also configure Grafana/Datadog and GitHub")


def _setup_posthog_mcp() -> None:
    # Transport is fixed to Streamable HTTP (the hosted PostHog MCP server). In
    # practice it is the only mode anyone selects, so the transport prompt was removed
    # on purpose — do NOT reintroduce a transport selection or a stdio branch here.
    mode = "streamable-http"
    credentials: dict[str, Any] = {"mode": mode, "read_only": True}
    url = _p("PostHog MCP URL", default=DEFAULT_POSTHOG_MCP_URL)
    if not url:
        _die("url is required for remote MCP modes.")
    credentials["url"] = url
    credentials["command"] = ""
    credentials["args"] = []

    credentials["auth_token"] = _p("PostHog personal API key (MCP Server preset)", secret=True)
    if not credentials["auth_token"]:
        _die("a personal API key is required for the hosted PostHog MCP server.")
    credentials["project_id"] = _p("PostHog project ID (optional)", default="")

    print("\n  Validating PostHog MCP...")
    config = build_posthog_mcp_config(credentials)
    result = validate_posthog_mcp_config(config)
    print(f"  {result.detail}")
    if not result.ok:
        sys.exit(1)

    upsert_integration("posthog_mcp", {"credentials": credentials})
    print("  Next:")
    print("    - opensre integrations verify posthog_mcp")


def _setup_sentry_mcp() -> None:
    # Transport is fixed to Streamable HTTP (the hosted Sentry MCP server). In
    # practice it is the only mode anyone selects, so the transport prompt was removed
    # on purpose — do NOT reintroduce a transport selection or a stdio branch here.
    mode = "streamable-http"
    credentials: dict[str, Any] = {"mode": mode}
    url = _p("Sentry MCP URL", default=DEFAULT_SENTRY_MCP_URL)
    if not url:
        _die("url is required for remote MCP modes.")
    credentials["url"] = url
    credentials["command"] = ""
    credentials["args"] = []

    credentials["auth_token"] = _p("Sentry user auth token", secret=True)
    if not credentials["auth_token"]:
        _die("a user auth token is required for the hosted Sentry MCP server.")
    credentials["host"] = _p("Self-hosted Sentry host (optional)", default="")

    print("\n  Validating Sentry MCP...")
    config = build_sentry_mcp_config(credentials)
    result = validate_sentry_mcp_config(config)
    print(f"  {result.detail}")
    if not result.ok:
        sys.exit(1)

    upsert_integration("sentry_mcp", {"credentials": credentials})
    print("  Next:")
    print("    - opensre integrations verify sentry_mcp")


def _setup_x_mcp() -> None:
    # X's MCP server (https://github.com/xdevplatform/xmcp) runs locally by
    # default, optionally tunneled for remote access — it is not an
    # always-on hosted endpoint like PostHog/Sentry's. Streamable HTTP is
    # the transport used by both a bare local server and a tunneled one, so
    # it stays the default here; do NOT add a transport prompt.
    mode = "streamable-http"
    credentials: dict[str, Any] = {"mode": mode}
    url = _p("X MCP URL", default=DEFAULT_X_MCP_URL)
    if not url:
        _die("url is required for remote MCP modes.")
    credentials["url"] = url
    credentials["command"] = ""
    credentials["args"] = []

    credentials["auth_token"] = _p(
        "Auth token for a tunneled/proxied endpoint (optional)", secret=True, default=""
    )
    credentials["bearer_token"] = ""

    print("\n  Validating X MCP...")
    config = build_x_mcp_config(credentials)
    result = validate_x_mcp_config(config)
    print(f"  {result.detail}")
    if not result.ok:
        sys.exit(1)

    upsert_integration("x_mcp", {"credentials": credentials})
    print("  Next:")
    print("    - opensre integrations verify x_mcp")


def _setup_postgresql() -> None:
    host = _p("Host (e.g. localhost or postgres.example.com)")
    database = _p("Database name")
    if not host or not database:
        _die("host and database are required.")
    port = _p("Port", default="5432")
    username = _p("Username", default="postgres")
    password = _p("Password", secret=True)
    ssl_mode_choice = _select(
        "SSL mode",
        choices=[
            questionary.Choice("prefer (recommended)", value="prefer"),
            questionary.Choice("require", value="require"),
            questionary.Choice("disable", value="disable"),
        ],
        instruction="(use arrow keys)",
    )
    if ssl_mode_choice is None:
        print("\nAborted.")
        sys.exit(1)
    upsert_integration(
        "postgresql",
        {
            "credentials": {
                "host": host,
                "port": int(port) if port.isdigit() else 5432,
                "database": database,
                "username": username or "postgres",
                "password": password,
                "ssl_mode": ssl_mode_choice,
            }
        },
    )


def _setup_mysql() -> None:
    host = _p("Host (e.g. localhost or mysql.example.com)")
    database = _p("Database name")
    if not host or not database:
        _die("host and database are required.")
    port = _p("Port", default="3306")
    username = _p("Username", default="root")
    password = _p("Password", secret=True)
    ssl_mode_choice = _select(
        "SSL mode",
        choices=[
            questionary.Choice("preferred (encrypted, no cert verification)", value="preferred"),
            questionary.Choice("required", value="required"),
            questionary.Choice("disabled", value="disabled"),
        ],
        instruction="(use arrow keys)",
    )
    if ssl_mode_choice is None:
        print("\nAborted.")
        sys.exit(1)
    upsert_integration(
        "mysql",
        {
            "credentials": {
                "host": host,
                "port": int(port) if port.isdigit() else 3306,
                "database": database,
                "username": username or "root",
                "password": password,
                "ssl_mode": ssl_mode_choice,
            }
        },
    )


def _setup_mongodb_atlas() -> None:
    from integrations.mongodb_atlas.setup import MONGODB_ATLAS_SETUP

    _run_spec_setup(MONGODB_ATLAS_SETUP)


def _setup_mariadb() -> None:
    host = _p("Host (e.g. db.example.com)")
    port = _p("Port", default="3306")
    database = _p("Database name")
    username = _p("Username")
    password = _p("Password", secret=True)
    ssl_choice = _select(
        "SSL enabled?",
        choices=[
            questionary.Choice("Yes", value="1"),
            questionary.Choice("No", value="0"),
        ],
        instruction="(use arrow keys)",
    )
    if ssl_choice is None:
        print("\nAborted.")
        sys.exit(1)
    ssl = ssl_choice == "1"
    if not host or not database or not username:
        _die("host, database, and username are required.")
    upsert_integration(
        "mariadb",
        {
            "credentials": {
                "host": host,
                "port": _parse_port(port),
                "database": database,
                "username": username,
                "password": password,
                "ssl": ssl,
            }
        },
    )


def _setup_alertmanager() -> None:
    base_url = _p("Alertmanager URL (e.g. http://alertmanager:9093)")
    if not base_url:
        _die("base_url is required.")

    auth_choice = _select(
        "Authentication method:",
        choices=[
            questionary.Choice("None (unauthenticated / internal network)", value="none"),
            questionary.Choice("Bearer token (reverse proxy auth)", value="bearer"),
            questionary.Choice("Basic auth (username + password)", value="basic"),
        ],
        instruction="(use arrow keys)",
    )
    if auth_choice is None:
        print("\nAborted.")
        sys.exit(1)

    credentials: dict[str, Any] = {"base_url": base_url}

    if auth_choice == "bearer":
        bearer_token = _p("Bearer token", secret=True)
        if not bearer_token:
            _die("Bearer token is required for bearer auth.")
        credentials["bearer_token"] = bearer_token
    elif auth_choice == "basic":
        username = _p("Username")
        if not username:
            _die("Username is required for basic auth.")
        credentials["username"] = username
        credentials["password"] = _p("Password", secret=True)

    upsert_integration("alertmanager", {"credentials": credentials})


def _setup_signoz() -> None:
    from integrations.signoz.setup import SIGNOZ_SETUP

    _run_spec_setup(SIGNOZ_SETUP)


def _setup_jenkins() -> None:
    from integrations.jenkins.setup import JENKINS_SETUP

    _run_spec_setup(JENKINS_SETUP)


def _setup_helm() -> None:
    from integrations.helm.setup import HELM_SETUP

    _run_spec_setup(HELM_SETUP)


def _setup_tempo() -> None:
    from integrations.tempo.setup import TEMPO_SETUP

    _run_spec_setup(TEMPO_SETUP)


def _setup_pagerduty() -> None:
    from integrations.pagerduty.setup import PAGERDUTY_SETUP

    _run_spec_setup(PAGERDUTY_SETUP)


def _setup_kubernetes() -> None:
    kubeconfig_path = _p(
        "Kubeconfig file path (e.g. ~/.kube/config) — leave empty to paste inline YAML",
        default="",
    )
    kubeconfig_content = ""
    if not kubeconfig_path:
        kubeconfig_content = _p(
            "Paste raw kubeconfig YAML content (required if no file path given)",
            default="",
        )
        if not kubeconfig_content:
            _die("Either a kubeconfig file path or inline YAML content is required.")
    context = _p(
        "Kubeconfig context to use (leave empty to use the current-context from the file)",
        default="",
    )
    namespace = _p("Default namespace", default="default")
    upsert_integration(
        "kubernetes",
        {
            "credentials": {
                "kubeconfig_path": kubeconfig_path,
                "kubeconfig": kubeconfig_content,
                "context": context,
                "namespace": namespace or "default",
            }
        },
    )


_HANDLERS: dict[str, Any] = {
    "alertmanager": _setup_alertmanager,
    "aws": _setup_aws,
    "betterstack": _setup_betterstack,
    "coralogix": _setup_coralogix,
    "datadog": _setup_datadog,
    "groundcover": _setup_groundcover,
    "grafana": _setup_grafana,
    "honeycomb": _setup_honeycomb,
    "helm": _setup_helm,
    "incident_io": _setup_incident_io,
    "mariadb": _setup_mariadb,
    "mongodb_atlas": _setup_mongodb_atlas,
    "slack": _setup_slack,
    "opensearch": _setup_opensearch,
    "rds": _setup_rds,
    "tracer": _setup_tracer,
    "vercel": _setup_vercel,
    "github": _setup_github,
    "gitlab": _setup_gitlab,
    "sentry": _setup_sentry,
    "posthog": _setup_posthog,
    "mongodb": _setup_mongodb,
    "discord": _setup_discord,
    "telegram": _setup_telegram,
    "rocketchat": _setup_rocketchat,
    "smtp": _setup_smtp,
    "whatsapp": _setup_whatsapp,
    "twilio": _setup_twilio,
    "openclaw": _setup_openclaw,
    "posthog_mcp": _setup_posthog_mcp,
    "sentry_mcp": _setup_sentry_mcp,
    "x_mcp": _setup_x_mcp,
    "postgresql": _setup_postgresql,
    "mysql": _setup_mysql,
    "redis": _setup_redis,
    "signoz": _setup_signoz,
    "jenkins": _setup_jenkins,
    "tempo": _setup_tempo,
    "pagerduty": _setup_pagerduty,
    "kubernetes": _setup_kubernetes,
    "servicenow": _setup_servicenow,
}


def _setup_dagster() -> None:
    from integrations.dagster.setup import DAGSTER_SETUP

    _run_spec_setup(DAGSTER_SETUP)


_HANDLERS["dagster"] = _setup_dagster


def _setup_temporal() -> None:
    from integrations.temporal.setup import TEMPORAL_SETUP

    _run_spec_setup(TEMPORAL_SETUP)


_HANDLERS["temporal"] = _setup_temporal


def _setup_azure_sql() -> None:
    server = _p("Server (e.g. myserver.database.windows.net)")
    database = _p("Database name")
    if not server or not database:
        _die("server and database are required.")
    port = _p("Port", default="1433")
    username = _p("Username")
    password = _p("Password", secret=True)
    driver = _p("ODBC driver", default="ODBC Driver 18 for SQL Server")
    encrypt_choice = _select(
        "Encrypt connection?",
        choices=[
            questionary.Choice("Yes (recommended for Azure)", value="1"),
            questionary.Choice("No", value="0"),
        ],
        instruction="(use arrow keys)",
    )
    if encrypt_choice is None:
        print("\nAborted.")
        sys.exit(1)
    encrypt = encrypt_choice == "1"
    upsert_integration(
        "azure_sql",
        {
            "credentials": {
                "server": server,
                "port": _parse_port(port, default=1433),
                "database": database,
                "username": username,
                "password": password,
                "driver": driver or "ODBC Driver 18 for SQL Server",
                "encrypt": encrypt,
            }
        },
    )


_HANDLERS["azure_sql"] = _setup_azure_sql

_SETUP_SERVICES = tuple(service for service in SUPPORTED_SETUP_SERVICES if service in _HANDLERS)


SUPPORTED = ", ".join(_SETUP_SERVICES)
SUPPORTED_VERIFY = ", ".join(SUPPORTED_VERIFY_SERVICES)


def cmd_setup(service: str | None) -> str:
    if not service:
        try:
            service = _select(
                "Which service would you like to set up?",
                choices=list(_SETUP_SERVICES),
                instruction="(use arrow keys)",
            )
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)
    if service:
        service = resolve_management_service(service)
    if not service or service not in _SETUP_SERVICES:
        _die(f"Usage: setup <service>. Supported: {SUPPORTED}")
    print(f"\n  Setting up {_B}{service}{_R}\n")
    _HANDLERS[service]()
    print(f"\n  ✓ Saved → {STORE_PATH}\n")
    return service


def cmd_list() -> None:
    from platform.common.runtime_flags import is_json_output

    items = list_integrations()

    if is_json_output():
        _json_echo(items)
        return

    if not items:
        print(
            "  No integrations. Run: opensre integrations setup <service>, "
            "or opensre onboard for the guided wizard."
        )
        return

    from rich.markup import escape

    from integrations._table_render import new_table, render_table
    from platform.terminal.theme import GLYPH_SUCCESS, HIGHLIGHT, SECONDARY, TEXT

    table = new_table()
    table.add_column("SERVICE", style=TEXT, no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("ID", style=SECONDARY)
    for i in items:
        status = i["status"]
        status_cell = (
            f"[bold {HIGHLIGHT}]{GLYPH_SUCCESS} {escape(status)}[/]"
            if status == "active"
            else escape(status)
        )
        table.add_row(escape(i["service"]), status_cell, escape(i["id"]))

    print(render_table(table))


def cmd_show(service: str | None) -> None:
    if not service:
        _die("Usage: show <service>")
        return
    service = resolve_management_service(service)
    record = get_integration(service)
    if not record:
        _die(f"No active integration for '{service}'.")
        return
    _json_echo(_mask(record))


def cmd_remove(service: str | None) -> None:
    from platform.common.runtime_flags import is_yes

    if not service:
        _die("Usage: remove <service>")
        return
    service = resolve_management_service(service)
    if not is_yes():
        try:
            confirmed = _confirm(f"Remove '{service}'?", default=False)
        except (EOFError, KeyboardInterrupt):
            return
        if not confirmed:
            print("  Cancelled.")
            return
    if remove_integration(service):
        print(f"  ✓ Removed '{service}'.")
    else:
        print(f"  No integration found for '{service}'.")


def cmd_verify(service: str | None, *, send_slack_test: bool = False) -> int:
    from platform.common.runtime_flags import is_json_output

    if service:
        service = resolve_management_service(service)
    if service and service not in SUPPORTED_VERIFY_SERVICES:
        _die(f"Usage: verify [service]. Supported: {SUPPORTED_VERIFY}")

    results = verify_integrations(service=service, send_slack_test=send_slack_test)

    if is_json_output():
        _json_echo(results)
    else:
        print(format_verification_results(results))
    return verification_exit_code(results, requested_service=service)
