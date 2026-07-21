"""Integration tests for CLI → harness port wiring."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import platform.harness_ports as harness_ports
from integrations.tracer.integrations_adapter import fetch_tracer_remote_integrations
from platform.observability import NoopProgressTracker
from platform.observability.render import debug as obs_debug
from platform.observability.render import display as obs_display
from platform.observability.render import progress as obs_progress
from platform.observability.render.debug import set_debug_printer
from platform.observability.render.display import (
    set_investigation_footer_renderer,
    set_investigation_header_renderer,
)
from platform.observability.render.progress import (
    set_progress_tracker,
    set_progress_tracker_factory,
)
from surfaces.interactive_shell.ui.output import boundary as output_boundary


def _reset_all_ports() -> None:
    harness_ports.reset_harness_ports()
    set_progress_tracker(NoopProgressTracker())
    set_progress_tracker_factory(None)
    obs_progress._silenced = False
    set_debug_printer(obs_debug._default_debug_printer)
    set_investigation_header_renderer(obs_display._default_header_renderer)
    set_investigation_footer_renderer(obs_display._default_footer_renderer)


@pytest.fixture(autouse=True)
def _reset_integrations_port() -> Iterator[None]:
    _reset_all_ports()
    yield
    _reset_all_ports()


def test_port_defaults_to_empty_before_boundary_install() -> None:
    assert harness_ports.fetch_remote_integrations(org_id="org-1", auth_token="tok") == []


def test_install_product_adapters_wires_tracer_fetcher() -> None:
    output_boundary.install_product_adapters()

    assert harness_ports._fetch_remote is fetch_tracer_remote_integrations


def test_registered_fetcher_is_invoked() -> None:
    calls: list[tuple[str, str]] = []

    def _fake_fetcher(org_id: str, auth_token: str) -> list[dict[str, object]]:
        calls.append((org_id, auth_token))
        return [{"service": "grafana", "config": {}}]

    harness_ports.set_remote_integrations_fetcher(_fake_fetcher)
    result = harness_ports.fetch_remote_integrations(org_id="org-42", auth_token="jwt-here")

    assert calls == [("org-42", "jwt-here")]
    assert result == [{"service": "grafana", "config": {}}]


def test_reset_restores_webapp_vault_fetcher_default() -> None:
    # Arrange: register a distinctive fetcher that must not survive a reset —
    # if it leaks, every later test sees this instead of the noop default.
    def _sentinel_vault() -> list[dict[str, object]]:
        return [{"service": "leaked-vault-marker"}]

    harness_ports.set_integration_resolution_adapters(fetch_webapp_vault=_sentinel_vault)
    assert harness_ports._fetch_webapp_vault is _sentinel_vault

    # Act
    harness_ports.reset_harness_ports()

    # Assert: the noop default is restored, not the leaked sentinel.
    assert harness_ports._fetch_webapp_vault is harness_ports._default_fetch_webapp_vault


def test_install_harness_ports_wires_catalog_and_registry() -> None:
    output_boundary.install_harness_ports()

    assert harness_ports._load_integrations is not harness_ports._default_load_integrations
    assert isinstance(harness_ports.get_surface_tools("action"), list)


def test_install_harness_ports_wires_cli_llm_adapters() -> None:
    # Before wiring, the CLI-LLM backend fails loudly instead of silently no-op'ing.
    harness_ports.reset_harness_ports()
    with pytest.raises(RuntimeError, match="not registered"):
        harness_ports.build_cli_client(object(), model="x")

    output_boundary.install_harness_ports()

    assert (
        harness_ports._cli_provider_registration_fn
        is not harness_ports._default_cli_provider_registration
    )
    assert harness_ports._build_cli_client_fn is not harness_ports._cli_llm_backend_unavailable
    assert harness_ports._flatten_cli_messages_fn is not harness_ports._cli_llm_backend_unavailable
