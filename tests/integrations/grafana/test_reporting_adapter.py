"""Tests for the Grafana reporting adapter."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.config_models import GrafanaIntegrationConfig
from integrations.grafana.reporting_adapter import grafana_delivery_adapter


class _ClientFactorySpy:
    """Spy for the Grafana client factory to record calls and return a dummy client."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.client = object()

    def __call__(
        self,
        *,
        endpoint: str,
        api_key: str,
        account_id: str,
        username: str,
        password: str,
    ) -> object:
        self.calls.append(
            {
                "endpoint": endpoint,
                "api_key": api_key,
                "account_id": account_id,
                "username": username,
                "password": password,
            }
        )
        return self.client


class _SinkSpy:
    """Spy for the Grafana log sink to record calls to send_investigation_report."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_investigation_report(
        self,
        state: dict[str, Any],
        messages: dict[str, Any] | None = None,
    ) -> bool:
        self.calls.append({"state": state, "messages": messages})
        return True


class _SinkFactorySpy:
    """Spy for the Grafana log sink factory to record clients and return a dummy sink."""

    def __init__(self, sink: _SinkSpy) -> None:
        self.sink = sink
        self.clients: list[object] = []

    def __call__(self, client: object) -> _SinkSpy:
        self.clients.append(client)
        return self.sink


def _patch_adapter_deps(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_ClientFactorySpy, _SinkFactorySpy, _SinkSpy]:
    client_factory = _ClientFactorySpy()
    sink = _SinkSpy()
    sink_factory = _SinkFactorySpy(sink)
    monkeypatch.setattr(
        "integrations.grafana.client.get_grafana_client_from_credentials",
        client_factory,
    )
    monkeypatch.setattr("integrations.grafana.log_sink.GrafanaLogSink", sink_factory)
    return client_factory, sink_factory, sink


@pytest.mark.parametrize(
    "resolved",
    [{}, {"grafana": {"endpoint": ""}}],
    ids=["no-grafana", "no-endpoint"],
)
def test_skips_without_usable_grafana_config(
    monkeypatch: pytest.MonkeyPatch,
    resolved: dict[str, Any],
) -> None:
    client_factory, sink_factory, sink = _patch_adapter_deps(monkeypatch)
    state = {"resolved_integrations": resolved}

    assert grafana_delivery_adapter.deliver(state, messages={}, blocks=[]) is False
    assert client_factory.calls == []
    assert sink_factory.clients == []
    assert sink.calls == []


@pytest.mark.parametrize(
    ("resolved", "expected_call"),
    [
        (
            {"grafana": GrafanaIntegrationConfig(endpoint="https://g.example", api_key="k")},
            {
                "endpoint": "https://g.example",
                "api_key": "k",
                "account_id": "investigation_sink",
                "username": "",
                "password": "",
            },
        ),
        (
            {"grafana": {"endpoint": "https://g.example", "api_key": "k"}},
            {
                "endpoint": "https://g.example",
                "api_key": "k",
                "account_id": "investigation_sink",
                "username": "",
                "password": "",
            },
        ),
        (
            {
                "grafana_local": {
                    "endpoint": "http://localhost:3000",
                    "username": "u",
                    "password": "p",
                }
            },
            {
                "endpoint": "http://localhost:3000",
                "api_key": "",
                "account_id": "investigation_sink",
                "username": "u",
                "password": "p",
            },
        ),
    ],
    ids=["pydantic-model", "plain-dict", "grafana-local"],
)
def test_activates_with_supported_config_shapes(
    monkeypatch: pytest.MonkeyPatch,
    resolved: dict[str, Any],
    expected_call: dict[str, str],
) -> None:
    client_factory, sink_factory, sink = _patch_adapter_deps(monkeypatch)
    messages = {"slack_text": "summary"}
    state = {"resolved_integrations": resolved, "alert_name": "test-alert"}

    assert grafana_delivery_adapter.deliver(state, messages=messages, blocks=[]) is True
    assert client_factory.calls == [expected_call]
    assert sink_factory.clients == [client_factory.client]
    assert sink.calls == [{"state": state, "messages": messages}]
