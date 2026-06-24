"""Integration tests for CLI → integrations port wiring."""

from __future__ import annotations

import pytest

from app.cli.interactive_shell.ui.output import boundary as output_boundary
from app.integrations import port as integrations_port
from app.integrations.port import fetch_remote_integrations, set_remote_integrations_fetcher
from app.services.tracer_client.integrations_adapter import fetch_tracer_remote_integrations


@pytest.fixture(autouse=True)
def _reset_integrations_port() -> None:
    set_remote_integrations_fetcher(integrations_port._default_fetcher)


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
