"""Configurator handlers for log/metric/trace backends."""

from __future__ import annotations

from config.env_file import sync_env_values
from integrations.coralogix.setup import CORALOGIX_SETUP
from integrations.datadog.setup import DATADOG_SETUP
from integrations.grafana.setup import GRAFANA_SETUP
from integrations.honeycomb.setup import HONEYCOMB_SETUP
from integrations.opensearch.setup import OPENSEARCH_SETUP
from integrations.store import remove_integration, upsert_integration
from integrations.tempo.setup import TEMPO_SETUP
from platform.terminal.theme import ERROR, HIGHLIGHT, SECONDARY
from surfaces.cli.wizard._ui import (
    _confirm,
    _console,
    _integration_defaults,
    _prompt_value,
    _render_integration_result,
    _string_value,
)
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec
from surfaces.cli.wizard.integration_health import (
    validate_splunk_integration,
)


def _configure_grafana() -> tuple[str, str]:
    return configure_from_spec(GRAFANA_SETUP, title="Grafana")


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


_TEMPO_INTRO = (
    f"[{SECONDARY}]Tempo commonly runs without auth behind a gateway — a URL alone is enough.\n"
    "For auth, provide either a bearer token OR a username/password (not both).[/]"
)


def _configure_tempo() -> tuple[str, str]:
    return configure_from_spec(TEMPO_SETUP, title="Tempo", intro=_TEMPO_INTRO)


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
    return configure_from_spec(OPENSEARCH_SETUP, title="OpenSearch")
