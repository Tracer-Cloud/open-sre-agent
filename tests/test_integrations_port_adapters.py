"""Integration tests for CLI → integrations port wiring."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from cli.interactive_shell.ui.output import boundary as output_boundary
from integrations import port as integrations_port
from integrations.port import fetch_remote_integrations, set_remote_integrations_fetcher
from platform.observability import NoopProgressTracker
from platform.observability import debug as obs_debug
from platform.observability import display as obs_display
from platform.observability import progress as obs_progress
from platform.observability.debug import set_debug_printer
from platform.observability.display import (
    set_investigation_footer_renderer,
    set_investigation_header_renderer,
)
from platform.observability.progress import (
    set_progress_tracker,
    set_progress_tracker_factory,
)
from services.tracer_client.integrations_adapter import fetch_tracer_remote_integrations


def _reset_all_ports() -> None:
    """Restore every port + global to its no-op / default state.

    ``install_product_adapters`` wires four observability ports in
    addition to the integrations fetcher; resetting only the
    integrations fetcher would leave the other four registered for
    the rest of the pytest session. Symmetric with the helper in
    :mod:`tests.test_observability_adapters`.
    """
    set_remote_integrations_fetcher(integrations_port._default_fetcher)
    set_progress_tracker(NoopProgressTracker())
    set_progress_tracker_factory(None)
    obs_progress._silenced = False
    set_debug_printer(obs_debug._default_debug_printer)
    set_investigation_header_renderer(obs_display._default_header_renderer)
    set_investigation_footer_renderer(obs_display._default_footer_renderer)


@pytest.fixture(autouse=True)
def _reset_integrations_port() -> Iterator[None]:
    """Reset every port the boundary wires before AND after each test.

    The teardown matters: without it, the final test that calls
    ``install_product_adapters`` (e.g.
    ``test_install_product_adapters_wires_tracer_fetcher``) leaks the
    CLI debug printer, Rich header/footer renderers, and progress-tracker
    factory into the rest of the pytest session.
    """
    _reset_all_ports()
    yield
    _reset_all_ports()


def test_port_defaults_to_empty_before_boundary_install() -> None:
    assert fetch_remote_integrations(org_id="org-1", auth_token="tok") == []


def test_install_product_adapters_wires_tracer_fetcher() -> None:
    output_boundary.install_product_adapters()

    assert integrations_port._fetcher is fetch_tracer_remote_integrations


def test_registered_fetcher_is_invoked() -> None:
    calls: list[tuple[str, str]] = []

    def _fake_fetcher(org_id: str, auth_token: str) -> list[dict[str, object]]:
        calls.append((org_id, auth_token))
        return [{"service": "grafana", "config": {}}]

    set_remote_integrations_fetcher(_fake_fetcher)
    result = fetch_remote_integrations(org_id="org-42", auth_token="jwt-here")

    assert calls == [("org-42", "jwt-here")]
    assert result == [{"service": "grafana", "config": {}}]
