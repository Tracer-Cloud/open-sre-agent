"""Interactive quickstart flow for local LLM configuration."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlparse

import questionary
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from app.cli.interactive_shell.ui.theme import (
    BRAND,
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    GLYPH_WARNING,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)
from app.cli.wizard.config import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS, ProviderOption
from app.cli.wizard.env_sync import sync_env_values, sync_provider_env
from app.cli.wizard.integration_health import IntegrationHealthResult
from app.cli.wizard.probes import ProbeResult, probe_local_target, probe_remote_target
from app.cli.wizard.prompts import select as select_prompt
from app.cli.wizard.store import get_store_path, load_local_config, save_local_config
from app.cli.wizard.validation import build_demo_action_response as _build_demo_action_response
from app.integrations.llm_cli.binary_resolver import diagnose_binary_path
from app.integrations.store import get_integration, remove_integration, upsert_integration
from app.llm_credentials import (
    get_keyring_setup_instructions,
    has_llm_api_key,
    save_llm_api_key,
)
from app.version import get_version

_console = Console(
    highlight=False, force_terminal=True, color_system="truecolor", legacy_windows=False
)
DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
DEFAULT_GITHUB_MCP_MODE = "streamable-http"
DEFAULT_OPENCLAW_MCP_URL = "http://127.0.0.1:18789/"
DEFAULT_OPENCLAW_MCP_MODE = "stdio"
DEFAULT_OPENCLAW_MCP_COMMAND = "openclaw"
DEFAULT_OPENCLAW_MCP_ARGS = ("mcp", "serve")
DEFAULT_SENTRY_URL = "https://sentry.io"
DEFAULT_GITLAB_BASE_URL = "https://gitlab.com/api/v4"


# Re-export build_demo_action_response from validation as a stable module-level
# attribute. The wrapper indirection (instead of `from x import y`) is
# preserved so the function remains patchable via monkeypatch.setattr(flow,
# "build_demo_action_response", ...) — but we also keep the underlying import
# at module load time so the attribute exists immediately, even in CI parallel
# test workers where lazy imports inside the wrapper occasionally fail to
# materialize on first access.
def build_demo_action_response():
    return _build_demo_action_response()


def validate_grafana_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_grafana_integration as _validate

    return _validate(**kwargs)


def validate_datadog_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_datadog_integration as _validate

    return _validate(**kwargs)


def validate_honeycomb_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_honeycomb_integration as _validate

    return _validate(**kwargs)


def validate_coralogix_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_coralogix_integration as _validate

    return _validate(**kwargs)


def validate_slack_webhook(**kwargs):
    from app.cli.wizard.integration_health import validate_slack_webhook as _validate

    return _validate(**kwargs)


def validate_aws_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_aws_integration as _validate

    return _validate(**kwargs)


def validate_github_mcp_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_github_mcp_integration as _validate

    return _validate(**kwargs)


def validate_gitlab_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_gitlab_integration as _validate

    return _validate(**kwargs)


def validate_sentry_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_sentry_integration as _validate

    return _validate(**kwargs)


def _looks_like_openclaw_control_ui_url(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return False

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    return port == 18789 and parsed.path.rstrip("/") == ""


def validate_notion_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_notion_integration as _validate

    return _validate(**kwargs)


def validate_jira_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_jira_integration as _validate

    return _validate(**kwargs)


def validate_google_docs_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_google_docs_integration as _validate

    return _validate(**kwargs)


def validate_vercel_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_vercel_integration as _validate

    return _validate(**kwargs)


def validate_betterstack_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_betterstack_integration as _validate

    return _validate(**kwargs)


def validate_alertmanager_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_alertmanager_integration as _validate

    return _validate(**kwargs)


def validate_opensearch_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_opensearch_integration as _validate

    return _validate(**kwargs)


def validate_opsgenie_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_opsgenie_integration as _validate

    return _validate(**kwargs)


def validate_incident_io_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_incident_io_integration as _validate

    return _validate(**kwargs)


def validate_discord_bot(**kwargs):
    from app.cli.wizard.integration_health import validate_discord_bot as _validate

    return _validate(**kwargs)


def validate_openclaw_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_openclaw_integration as _validate

    return _validate(**kwargs)


def validate_splunk_integration(**kwargs):
    from app.cli.wizard.integration_health import validate_splunk_integration as _validate

    return _validate(**kwargs)


def get_sentry_auth_recommendations():
    from app.integrations.sentry import get_sentry_auth_recommendations as _get

    return _get()


_STYLE = questionary.Style(
    [
        ("qmark", f"fg:{HIGHLIGHT} bold"),
        ("question", f"fg:{TEXT} bold"),
        ("answer", f"fg:{BRAND} bold"),
        ("pointer", f"fg:{HIGHLIGHT} bold"),
        ("highlighted", f"fg:{TEXT} bg:{HIGHLIGHT} bold"),
        ("selected", f"fg:{TEXT} bg:default bold"),
        ("separator", f"fg:{DIM}"),
        ("text", f"fg:{TEXT} bg:default"),
        ("disabled", f"fg:{SECONDARY} bg:default italic"),
        ("instruction", f"fg:{SECONDARY} italic"),
    ]
)


@dataclass(frozen=True)
class Choice:
    """A selectable wizard choice."""

    value: str
    label: str
    group: str | None = None
    hint: str | None = None


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string_value(value: object, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _joined_values(value: object, *, separator: str, fallback: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return separator.join(value)
    return fallback


def _local_defaults() -> dict[str, str | bool | None]:
    stored = load_local_config(get_store_path())
    wizard = _as_mapping(stored.get("wizard"))
    targets = _as_mapping(stored.get("targets"))
    local = _as_mapping(targets.get("local"))
    raw_provider = local.get("provider")
    provider = PROVIDER_BY_VALUE.get(_string_value(raw_provider)) if raw_provider else None
    api_key_env = _string_value(local.get("api_key_env"), provider.api_key_env if provider else "")
    is_cli = bool(provider and provider.credential_kind == "cli")
    return {
        "wizard_mode": _string_value(wizard.get("mode"), "quickstart"),
        "provider": _string_value(raw_provider) if raw_provider else None,
        "model": _string_value(local.get("model")),
        "api_key_env": api_key_env,
        "has_api_key": True if is_cli else bool(api_key_env and has_llm_api_key(api_key_env)),
        "legacy_api_key": _string_value(local.get("api_key")),
    }


def _integration_defaults(service: str) -> tuple[Mapping[str, object], Mapping[str, object]]:
    entry = _as_mapping(get_integration(service))
    return entry, _as_mapping(entry.get("credentials"))


def _step(title: str) -> None:
    """Print a sub-step header in HIGHLIGHT with a DIM rule."""
    _console.print()
    t = Text()
    t.append("  ")
    t.append(title, style=f"bold {HIGHLIGHT}")
    _console.print(t)
    _console.print(Rule(style=DIM))


def _step_header(n: int, total: int, title: str) -> None:
    """Print a numbered wizard stage header.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
      Step 2/4  —  LLM Provider                  [SECONDARY "Step N/N"] [TEXT title]
      ─────────────────────────────────────────  [DIM rule]
    """
    _console.print()
    _console.print(Rule(style=DIM))
    header = Text()
    header.append("  ")
    header.append(f"Step {n}/{total}", style=f"bold {SECONDARY}")
    header.append("  —  ", style=DIM)
    header.append(title, style=f"bold {TEXT}")
    _console.print(header)
    _console.print(Rule(style=DIM))


def _choice_title(choice: Choice) -> str:
    return choice.label


def _choice_description(choice: Choice) -> str | None:
    if choice.hint:
        return choice.hint
    return choice.group


def _questionary_choice(choice: Choice) -> questionary.Choice:
    return questionary.Choice(
        title=_choice_title(choice),
        value=choice.value,
        description=_choice_description(choice),
    )


def _choose(prompt: str, choices: list[Choice], *, default: str | None = None) -> str:
    q_choices = [_questionary_choice(choice) for choice in choices]

    result = select_prompt(
        prompt,
        choices=q_choices,
        default=default,
        style=_STYLE,
        instruction="(Use arrows to move, Enter to choose)",
    ).ask()

    if result is None:
        raise KeyboardInterrupt
    return str(result)


def _confirm(prompt: str, *, default: bool = True) -> bool:
    result = questionary.confirm(prompt, default=default, style=_STYLE).ask()
    if result is None:
        raise KeyboardInterrupt
    return bool(result)


def _prompt_value(
    label: str,
    *,
    default: str = "",
    secret: bool = False,
    allow_empty: bool = False,
) -> str:
    while True:
        instruction = "(Enter to keep current)" if default else None
        if secret:
            result = questionary.password(
                label,
                default=default,
                style=_STYLE,
                instruction=instruction,
            ).ask()
        else:
            result = questionary.text(
                label,
                default=default,
                style=_STYLE,
                instruction=instruction,
            ).ask()

        if result is None:
            raise KeyboardInterrupt

        value = str(result).strip()
        if value:
            return value
        if default:
            return default
        if allow_empty:
            return ""
        _console.print(f"[{ERROR}]  {GLYPH_ERROR}  Required.[/]")


def _persist_llm_api_key(env_var: str, value: str) -> bool:
    try:
        save_llm_api_key(env_var, value)
    except RuntimeError as exc:
        _console.print(f"[{ERROR}]  {GLYPH_ERROR}  {exc}[/]")
        _console.print(
            f"[{WARNING}]  {GLYPH_WARNING}  OpenSRE could not save your API key to the local system keychain.[/]"
        )
        for line in get_keyring_setup_instructions(env_var):
            _console.print(f"[{SECONDARY}]    {line}[/]")
        return False
    return True


def _parse_csv_values(raw_value: str) -> list[str]:
    return [part.strip() for part in raw_value.split(",") if part.strip()]


def _display_probe(result: ProbeResult) -> None:
    status = f"[{HIGHLIGHT}]reachable[/]" if result.reachable else f"[{ERROR}]unreachable[/]"
    _console.print(f"{result.target}: {status} [{SECONDARY}]({result.detail})[/]")


def _select_target_for_advanced(local_probe: ProbeResult, remote_probe: ProbeResult) -> str | None:
    _console.print(f"\n[{SECONDARY}]reachability[/]")
    _display_probe(local_probe)
    _display_probe(remote_probe)

    target = _choose(
        "Choose a configuration target:",
        [
            Choice(value="local", label="Local machine"),
            Choice(value="remote", label="Remote target (future support)"),
        ],
        default="local",
    )
    if target == "local":
        return "local"

    _console.print(f"\n[{WARNING}]Remote setup is not available yet.[/]")
    if _confirm("Use local setup instead?", default=True):
        return "local"
    _console.print(f"[{WARNING}]Setup cancelled.[/]")
    return None


def _render_header() -> None:
    """Print the onboarding splash using the design-system palette.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
        ___                    ____  ____  _____ [HIGHLIGHT art]
       / _ \\ ...
      opensre  ·  v<version>                     [SECONDARY name] [DIM ·] [BRAND version]
      open-source SRE agent for automated …      [DIM description]
      ─────────────────────────────────────────  [DIM rule]
      Setup — Configure your local AI stack …    [SECONDARY subtitle]
    """
    from app.cli.interactive_shell.ui.banner import _render_art

    art = _render_art()
    version = get_version()

    _console.print()
    _console.print(Rule(style=DIM))
    _console.print()

    for line in art.splitlines():
        t = Text()
        t.append("  ")
        t.append(line, style=f"bold {HIGHLIGHT}")
        _console.print(t)

    _console.print()

    subtitle = Text()
    subtitle.append("  ")
    subtitle.append("opensre", style=SECONDARY)
    subtitle.append("  ·  ", style=DIM)
    subtitle.append(f"v{version}", style=BRAND)
    _console.print(subtitle)

    desc = Text()
    desc.append(
        "  open-source SRE agent for automated incident investigation and root cause analysis",
        style=DIM,
    )
    _console.print(desc)
    _console.print()
    _console.print(Rule(style=DIM))
    _console.print()

    setup_line = Text()
    setup_line.append("  Setup", style=f"bold {TEXT}")
    setup_line.append(
        "  —  Configure your local AI stack and optional integrations.", style=SECONDARY
    )
    _console.print(setup_line)
    _console.print()


def _render_saved_summary(
    *,
    provider_label: str,
    model: str,
    saved_path: str,
    env_path: str,
    configured_integrations: list[str],
    credential_line: str = "system keychain",
) -> None:
    """Print the post-onboarding success screen.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
      ✓  Done.                                   [HIGHLIGHT ✓ + text]
      ─────────────────────────────────────────  [DIM rule]
                                                 [blank]
        provider    Anthropic                    [SECONDARY key] [TEXT value]
        model       claude-opus-4-5              [SECONDARY key] [TEXT value]
        services    grafana · datadog            [SECONDARY key] [TEXT value]
        config      ~/.opensre/opensre.json      [SECONDARY key] [BRAND path]
        env         .env                         [SECONDARY key] [BRAND path]
        credentials system keychain              [SECONDARY key] [TEXT value]
        store       ~/.opensre/store.json        [SECONDARY key] [BRAND path]
    """
    from app.integrations.store import STORE_PATH

    integrations_str = "  ·  ".join(configured_integrations) if configured_integrations else "none"

    _console.print()
    _console.print(Rule(style=DIM))

    done = Text()
    done.append(f"  {GLYPH_SUCCESS}  ", style=f"bold {HIGHLIGHT}")
    done.append("Done.", style=f"bold {TEXT}")
    _console.print(done)

    _console.print(Rule(style=DIM))
    _console.print()

    key_col = 14

    def _kv(key: str, value: str, value_style: str = TEXT) -> None:
        row = Text()
        row.append(f"    {key:<{key_col}}", style=SECONDARY)
        row.append(value, style=value_style)
        _console.print(row)

    _kv("provider", provider_label)
    _kv("model", model)
    _kv("services", integrations_str)
    _kv("config", saved_path, BRAND)
    _kv("env", env_path, BRAND)
    _kv("credentials", credential_line)
    _kv("store", str(STORE_PATH), BRAND)
    _console.print()


def _render_integration_result(
    service_label: str,
    result: IntegrationHealthResult,
    *,
    github_display_level: str | None = None,
) -> None:
    if result.github_mcp is not None:
        from app.integrations.github_mcp import (
            GitHubMcpDisplayDetailLevel,
            print_github_mcp_validation_report,
        )

        print_github_mcp_validation_report(
            result.github_mcp,
            console=_console,
            detail_level=cast(
                GitHubMcpDisplayDetailLevel,
                github_display_level or "standard",
            ),
        )
        return
    ok = bool(result.ok)
    detail = str(result.detail)
    glyph = GLYPH_SUCCESS if ok else GLYPH_ERROR
    glyph_style = f"bold {HIGHLIGHT}" if ok else f"bold {ERROR}"
    prefix = "Connected" if ok else "Failed"

    status_line = Text()
    status_line.append(f"  {glyph}  ", style=glyph_style)
    status_line.append(f"{service_label}", style=f"bold {TEXT}")
    status_line.append("  ·  ", style=DIM)
    status_line.append(prefix, style=TEXT)
    _console.print(status_line)

    for raw_line in detail.splitlines():
        line = raw_line.strip()
        if line:
            detail_text = Text()
            detail_text.append(f"     {line}", style=SECONDARY)
            _console.print(detail_text)


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
            from app.cli.wizard.grafana_seed import seed_logs

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


def _configure_github_mcp() -> tuple[str, str]:
    _, credentials = _integration_defaults("github")
    default_mode = _string_value(credentials.get("mode"), DEFAULT_GITHUB_MCP_MODE)
    mode = _choose(
        "Choose the GitHub MCP transport:",
        [
            Choice(value="sse", label="SSE"),
            Choice(value="streamable-http", label="Streamable HTTP"),
            Choice(value="stdio", label="stdio"),
        ],
        default=default_mode,
    )

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
        auth_token = _prompt_value(
            "GitHub PAT / auth token (optional if the server already authenticates upstream)",
            default=_string_value(credentials.get("auth_token")),
            secret=True,
            allow_empty=True,
        )

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
            upsert_integration("github", {"credentials": credentials})
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
    default_mode = (
        DEFAULT_OPENCLAW_MCP_MODE
        if use_stdio_defaults
        else _string_value(credentials.get("mode"), DEFAULT_OPENCLAW_MCP_MODE)
    )

    while True:
        mode = _choose(
            "Choose the OpenClaw MCP transport:",
            [
                Choice(value="stdio", label="stdio (recommended)"),
                Choice(value="streamable-http", label="Streamable HTTP"),
                Choice(value="sse", label="SSE"),
            ],
            default=default_mode,
        )

        url = ""
        command = ""
        args: list[str] = []
        auth_token = ""
        if mode == "stdio":
            command = _prompt_value(
                "OpenClaw MCP command",
                default=(
                    DEFAULT_OPENCLAW_MCP_COMMAND
                    if use_stdio_defaults
                    else _string_value(credentials.get("command"), DEFAULT_OPENCLAW_MCP_COMMAND)
                ),
            )
            args_raw = _prompt_value(
                "OpenClaw MCP args",
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
                "OpenClaw MCP URL",
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

        with _console.status("Validating OpenClaw MCP integration...", spinner="dots"):
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
            env_path = sync_env_values(
                {
                    "OPENCLAW_MCP_URL": url,
                    "OPENCLAW_MCP_MODE": mode,
                    "OPENCLAW_MCP_AUTH_TOKEN": auth_token,
                    "OPENCLAW_MCP_COMMAND": command,
                    "OPENCLAW_MCP_ARGS": " ".join(args),
                }
            )
            return "OpenClaw", str(env_path)
        default_mode = mode
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
            env_path = sync_env_values(
                {
                    "GITLAB_BASE_URL": base_url,
                    "GITLAB_ACCESS_TOKEN": auth_token,
                }
            )
            return "Gitlab", str(env_path)
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
            env_path = sync_env_values(
                {
                    "INCIDENT_IO_API_KEY": api_key,
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
            from app.integrations.cli import _register_discord_slash_command

            _register_discord_slash_command(application_id, bot_token)
            env_path = sync_env_values(
                {
                    "DISCORD_BOT_TOKEN": bot_token,
                    "DISCORD_APPLICATION_ID": application_id,
                    "DISCORD_PUBLIC_KEY": public_key,
                    "DISCORD_DEFAULT_CHANNEL_ID": default_channel_id,
                }
            )
            return "Discord", str(env_path)
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
                env_values["OPENSEARCH_API_KEY"] = api_key
            if username:
                env_values["OPENSEARCH_USERNAME"] = username
                env_values["OPENSEARCH_PASSWORD"] = password
            env_path = sync_env_values(env_values)
            return "OpenSearch", str(env_path)
        _console.print(f"[{DIM}]Try again or press Ctrl+C to cancel.[/]")


def _configure_selected_integrations() -> tuple[list[str], str | None]:
    configured: list[str] = []
    last_env_path: str | None = None

    _console.print(
        f"[{SECONDARY}]Pick one integration to wire up now, or skip this step and come back later.[/]"
    )
    integration_choices = [
        Choice(
            value="grafana_local",
            label="Grafana Local (Docker)",
            hint="Starts Grafana + Loki and seeds demo alerts",
        ),
        Choice(
            value="grafana",
            label="Grafana Cloud / self-hosted",
            hint="Connect an existing Grafana instance",
        ),
        Choice(value="datadog", label="Datadog", hint="Logs, monitors, and Kubernetes context"),
        Choice(value="honeycomb", label="Honeycomb", hint="Query traces and spans from Honeycomb"),
        Choice(value="coralogix", label="Coralogix", hint="Query logs from Coralogix DataPrime"),
        Choice(value="slack", label="Slack", hint="Send findings to a webhook or channel"),
        Choice(
            value="discord",
            label="Discord",
            hint="Trigger investigations via slash commands and post findings to threads",
        ),
        Choice(value="aws", label="AWS", hint="Inspect CloudWatch, EKS, and account resources"),
        Choice(
            value="github", label="GitHub MCP", hint="Let the agent inspect repos, PRs, and issues"
        ),
        Choice(
            value="sentry", label="Sentry", hint="Investigate errors, events, and issue history"
        ),
        Choice(value="gitlab", label="Gitlab", hint="Let the agent inspect repos, PRs, and issues"),
        Choice(
            value="google_docs",
            label="Google Docs",
            hint="Create shareable incident postmortem reports",
        ),
        Choice(
            value="vercel",
            label="Vercel",
            hint=(
                "Deployments, build output, and logs tools; runtime-log API can lag the dashboard"
            ),
        ),
        Choice(
            value="betterstack",
            label="Better Stack Telemetry",
            hint="Query logs from Better Stack (ClickHouse SQL over HTTP)",
        ),
        Choice(
            value="jira",
            label="Jira",
            hint="File and update incident tickets automatically",
        ),
        Choice(
            value="alertmanager",
            label="Alertmanager",
            hint="Query firing alerts and silences from Prometheus Alertmanager",
        ),
        Choice(
            value="opsgenie",
            label="OpsGenie",
            hint="Investigate alerts and triage state from OpsGenie",
        ),
        Choice(
            value="incident_io",
            label="incident.io",
            hint="Read incident context and updates from incident.io",
        ),
        Choice(
            value="notion",
            label="Notion",
            hint="Post investigation reports to a Notion database",
        ),
        Choice(
            value="openclaw",
            label="OpenClaw",
            hint="Connect OpenSRE to OpenClaw so your AI coding assistant can trigger investigations",
        ),
        Choice(value="splunk", label="Splunk", hint="Query logs from Splunk"),
        Choice(
            value="opensearch",
            label="OpenSearch / Elasticsearch",
            hint="Query logs and indices from OpenSearch or Elasticsearch clusters",
        ),
        Choice(
            value="skip",
            label="Skip for now",
            hint="Finish onboarding without configuring an integration",
        ),
    ]
    selected_service = _choose(
        "Choose an integration to configure",
        integration_choices,
        default="grafana_local",
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
        "aws": _configure_aws,
        "github": _configure_github_mcp,
        "sentry": _configure_sentry,
        "gitlab": _configure_gitlab,
        "google_docs": _configure_google_docs,
        "vercel": _configure_vercel,
        "betterstack": _configure_betterstack,
        "jira": _configure_jira,
        "alertmanager": _configure_alertmanager,
        "opsgenie": _configure_opsgenie,
        "incident_io": _configure_incident_io,
        "notion": _configure_notion,
        "openclaw": _configure_openclaw,
        "opensearch": _configure_opensearch,
        "splunk": _configure_splunk,
    }
    _SERVICE_LABELS = {
        "grafana_local": "grafana local",
        "grafana": "grafana",
        "datadog": "datadog",
        "honeycomb": "honeycomb",
        "coralogix": "coralogix",
        "slack": "slack",
        "discord": "discord",
        "aws": "aws",
        "github": "github mcp",
        "sentry": "sentry",
        "gitlab": "gitlab",
        "google_docs": "google docs",
        "vercel": "vercel",
        "jira": "jira",
        "alertmanager": "alertmanager",
        "opsgenie": "opsgenie",
        "incident_io": "incident.io",
        "notion": "notion",
        "openclaw": "openclaw",
        "opensearch": "opensearch",
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


def _render_next_steps() -> None:
    """Print the 'What's next' section after successful onboarding.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
      What's next                                [SECONDARY label]
      ─────────────────────────────────────────  [DIM rule]
        opensre                                  [BRAND runnable command]
          Start the interactive agent
        opensre investigate -i alert.json        [BRAND runnable command]
          Run root-cause analysis on an alert
        opensre doctor                           [BRAND runnable command]
          Verify your environment setup
    """
    _console.print(Rule(style=DIM))

    section = Text()
    section.append("  What's next", style=SECONDARY)
    _console.print(section)

    _console.print(Rule(style=DIM))
    _console.print()

    _NEXT: tuple[tuple[str, str], ...] = (
        ("opensre", "Start the interactive agent"),
        (
            "opensre investigate -i tests/e2e/kubernetes/fixtures/datadog_k8s_alert.json",
            "Run root-cause analysis on a sample alert",
        ),
        ("opensre doctor", "Verify your full environment setup"),
        ("opensre onboard", "Re-run this setup at any time"),
    )

    for cmd, description in _NEXT:
        cmd_line = Text()
        cmd_line.append(f"  {cmd}", style=f"bold {BRAND}")
        _console.print(cmd_line)
        desc_line = Text()
        desc_line.append(f"    {description}", style=SECONDARY)
        _console.print(desc_line)

    _console.print()


def _credential_line_for_saved_summary(provider: ProviderOption) -> str:
    """One-line credential description for the post-wizard saved summary."""
    if provider.credential_kind != "cli":
        return "system keychain"
    if provider.adapter_factory is None:
        return f"{provider.label} (CLI)"
    cli_adapter = provider.adapter_factory()
    return f"{provider.label} ({cli_adapter.auth_hint})"


def _run_cli_llm_onboarding(provider: ProviderOption) -> Literal["ok", "abort", "repick"]:
    """Probe CLI binary + auth; recovery menu when missing. ``repick`` = choose another LLM."""
    factory = provider.adapter_factory
    if factory is None:
        _console.print(
            f"[{ERROR}]  {GLYPH_ERROR}  Internal error: CLI provider missing adapter factory.[/]"
        )
        return "abort"
    adapter = factory()
    env_key = adapter.binary_env_key
    install_hint = adapter.install_hint
    auth_hint = adapter.auth_hint
    name = adapter.name
    for _attempt in range(10):
        probe = adapter.detect()
        if probe.installed and probe.logged_in is True:
            _console.print(f"[{SECONDARY}]{probe.detail}[/]")
            return "ok"
        if probe.installed and probe.logged_in is not True:
            _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
            status_prompt = (
                f"{provider.label} requires login. What next?"
                if probe.logged_in is False
                else f"Could not verify {provider.label} login. What next?"
            )
            action = _choose(
                status_prompt,
                [
                    Choice(
                        value="retry",
                        label="Re-detect after logging in",
                        hint=auth_hint,
                    ),
                    Choice(
                        value="repick",
                        label="Pick a different LLM provider",
                        hint=None,
                    ),
                ],
                default="retry",
            )
            if action == "repick":
                return "repick"
            continue
        _console.print(f"[{WARNING}]  {GLYPH_WARNING}  {probe.detail}[/]")
        action = _choose(
            f"{provider.label} not found. What next?",
            [
                Choice(
                    value="retry",
                    label="Re-detect after install",
                    hint=install_hint,
                ),
                Choice(
                    value="path",
                    label="Enter full path to the binary",
                    hint=f"Writes {env_key} to .env",
                ),
                Choice(
                    value="repick",
                    label="Pick a different LLM provider",
                    hint=None,
                ),
            ],
            default="retry",
        )
        if action == "repick":
            return "repick"
        if action == "path":
            path = _prompt_value(f"Full path to {name} binary")
            reason = diagnose_binary_path(path)
            if reason:
                _console.print(f"[{WARNING}]{reason} Try again.[/]")
                continue
            sync_env_values({env_key: path})
            os.environ[env_key] = path
            continue
        _console.print(f"[{SECONDARY}]    Hint: {install_hint}[/]")
    _console.print(f"[{WARNING}]  {GLYPH_WARNING}  Too many retry attempts. Aborting setup.[/]")
    return "abort"


def run_wizard(_argv: list[str] | None = None) -> int:
    """Run the interactive wizard."""
    _render_header()
    defaults = _local_defaults()
    saved_provider_value = defaults["provider"] if isinstance(defaults["provider"], str) else None
    saved_model_value = defaults["model"] if isinstance(defaults["model"], str) else ""
    default_wizard_mode = (
        defaults["wizard_mode"] if isinstance(defaults["wizard_mode"], str) else "quickstart"
    )
    default_provider_value = (
        saved_provider_value
        if saved_provider_value in PROVIDER_BY_VALUE
        else SUPPORTED_PROVIDERS[0].value
    )

    _step_header(1, 4, "Setup Mode")
    wizard_mode = _choose(
        "How do you want to get started?",
        [
            Choice(
                value="quickstart", label="Quickstart", hint="Local setup with the usual defaults"
            ),
            Choice(
                value="advanced",
                label="Advanced",
                hint="Show probes and choose the target explicitly",
            ),
        ],
        default=default_wizard_mode,
    )

    store_path = get_store_path()
    local_probe = probe_local_target(store_path)
    remote_probe = ProbeResult(
        target="remote",
        reachable=False,
        detail="Remote probing is shown during Advanced setup.",
    )

    if wizard_mode == "advanced":
        remote_probe = probe_remote_target()
        target = _select_target_for_advanced(local_probe, remote_probe)
        if target is None:
            return 1
    else:
        target = "local"

    if target != "local":
        print("Only local configuration is supported today.", file=sys.stderr)
        return 1

    force_repick = False
    provider: ProviderOption
    model: str
    while True:
        _step_header(2, 4, "LLM Provider")
        saved_provider = (
            PROVIDER_BY_VALUE.get(saved_provider_value) if saved_provider_value else None
        )
        if saved_provider is not None and not force_repick:
            current_model = saved_model_value or saved_provider.default_model
            _console.print(
                f"[{SECONDARY}]current provider  {saved_provider.label}  ·  {current_model}[/]"
            )
            change_provider = _confirm("Change provider?", default=False)
        else:
            change_provider = True
        force_repick = False

        if change_provider:
            provider = PROVIDER_BY_VALUE[
                _choose(
                    "Choose your LLM provider",
                    [
                        Choice(
                            value=p.value,
                            label=p.label,
                            hint=p.group,
                        )
                        for p in SUPPORTED_PROVIDERS
                    ],
                    default=default_provider_value,
                )
            ]
            model = provider.default_model
            if provider.credential_kind not in ("cli", "none"):
                _step(provider.credential_label.title())
                try:
                    api_key = _prompt_value(
                        f"{provider.label} {provider.credential_label} ({provider.api_key_env})",
                        default=provider.credential_default,
                        secret=provider.credential_secret,
                    )
                except KeyboardInterrupt:
                    _console.print(f"\n[{WARNING}]Setup cancelled.[/]")
                    return 1
                if not _persist_llm_api_key(provider.api_key_env, api_key):
                    return 1
        else:
            assert saved_provider is not None
            provider = saved_provider
            model = saved_model_value or provider.default_model
            if provider.credential_kind not in ("cli", "none"):
                has_api_key = bool(defaults["has_api_key"])
                legacy_api_key = str(defaults["legacy_api_key"] or "").strip()
                if not has_api_key and legacy_api_key:
                    if not _persist_llm_api_key(provider.api_key_env, legacy_api_key):
                        return 1
                    has_api_key = True
                if not has_api_key:
                    _step(provider.credential_label.title())
                    try:
                        api_key = _prompt_value(
                            f"{provider.label} {provider.credential_label} ({provider.api_key_env})",
                            default=provider.credential_default,
                            secret=provider.credential_secret,
                        )
                    except KeyboardInterrupt:
                        _console.print(f"\n[{WARNING}]Setup cancelled.[/]")
                        return 1
                    if not _persist_llm_api_key(provider.api_key_env, api_key):
                        return 1

        if provider.credential_kind == "cli":
            cli_out = _run_cli_llm_onboarding(provider)
            if cli_out == "abort":
                return 1
            if cli_out == "repick":
                force_repick = True
                continue
        break

    probes = {
        "local": local_probe.as_dict(),
        "remote": remote_probe.as_dict(),
    }
    saved_path = save_local_config(
        wizard_mode=wizard_mode,
        provider=provider.value,
        model=model,
        api_key_env=provider.api_key_env,
        model_env=provider.model_env,
        probes=probes,
    )
    env_path = sync_provider_env(provider=provider, model=model)

    _step_header(3, 4, "Integrations")
    try:
        configured_integrations, integration_env_path = _configure_selected_integrations()
    except KeyboardInterrupt:
        cancelled = Text()
        cancelled.append(f"\n  {GLYPH_WARNING}  ", style=f"bold {WARNING}")
        cancelled.append("Integration setup cancelled. AI config was kept.", style=TEXT)
        _console.print(cancelled)
        configured_integrations = []
        integration_env_path = None

    summary_env_path = integration_env_path or str(env_path)

    _render_saved_summary(
        provider_label=provider.label,
        model=model,
        saved_path=str(saved_path),
        env_path=summary_env_path,
        configured_integrations=configured_integrations,
        credential_line=_credential_line_for_saved_summary(provider),
    )
    _render_next_steps()
    return 0
