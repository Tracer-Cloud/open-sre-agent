"""Configurator handlers for log/metric/trace backends."""

from __future__ import annotations

from config.env_file import sync_env_secret, sync_env_values
from integrations.coralogix.setup import CORALOGIX_SETUP
from integrations.datadog.setup import DATADOG_SETUP
from integrations.honeycomb.setup import HONEYCOMB_SETUP
from integrations.store import remove_integration, upsert_integration
from platform.terminal.theme import DIM, ERROR, GLYPH_ERROR, HIGHLIGHT, SECONDARY
from surfaces.cli.wizard._ui import (
    Choice,
    _choose,
    _confirm,
    _console,
    _integration_defaults,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec
from surfaces.cli.wizard.integration_health import (
    validate_grafana_integration,
    validate_opensearch_integration,
    validate_splunk_integration,
    validate_tempo_integration,
)


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
        with _console.status("Validating Grafana integration...", spinner="dots"):
            result = validate_grafana_integration(
                endpoint=endpoint,
                api_key=api_key,
                verify_ssl=verify_ssl,
                ca_bundle=ca_bundle,
            )
        _render_integration_result("Grafana", result)
        if result.ok:
            upsert_integration(
                "grafana",
                {
                    "credentials": {
                        "endpoint": endpoint,
                        "api_key": api_key,
                        "verify_ssl": verify_ssl,
                        "ca_bundle": ca_bundle,
                    }
                },
            )
            env_values: dict[str, str] = {
                "GRAFANA_INSTANCE_URL": endpoint,
                "GRAFANA_VERIFY_SSL": "true" if verify_ssl else "false",
            }
            if ca_bundle:
                env_values["GRAFANA_CA_BUNDLE"] = ca_bundle
            env_path = sync_env_values(env_values)
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

    compose_file = str(Path(__file__).parent.parent / "local_grafana_stack/docker-compose.yml")
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
            from surfaces.cli.wizard.grafana_seed import seed_logs

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
    return configure_from_spec(DATADOG_SETUP, title="Datadog")


def _configure_honeycomb() -> tuple[str, str]:
    return configure_from_spec(HONEYCOMB_SETUP, title="Honeycomb")


def _configure_coralogix() -> tuple[str, str]:
    return configure_from_spec(CORALOGIX_SETUP, title="Coralogix")


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
                sync_env_secret("TEMPO_API_KEY", api_key)
            if username:
                env_values["TEMPO_USERNAME"] = username
            if password:
                sync_env_secret("TEMPO_PASSWORD", password)
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
