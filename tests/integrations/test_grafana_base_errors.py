"""Regression tests: Grafana client must not swallow API failures as empty data."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from integrations.grafana.base import GrafanaClientBase
from integrations.grafana.config import GrafanaAccountConfig


def _client() -> GrafanaClientBase:
    config = GrafanaAccountConfig(
        account_id="test",
        instance_url="https://grafana.example.com",
        read_token="token",
    )
    return GrafanaClientBase(config=config)


@patch("integrations.grafana.base.requests.get")
def test_query_alert_rules_propagates_http_error(mock_get: MagicMock) -> None:
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("401 Client Error")
    mock_get.return_value = response

    with pytest.raises(requests.HTTPError):
        _client().query_alert_rules()


@patch("integrations.grafana.base.requests.get")
def test_query_annotations_propagates_http_error(mock_get: MagicMock) -> None:
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("401 Client Error")
    mock_get.return_value = response

    with pytest.raises(requests.HTTPError):
        _client().query_annotations(from_ts=0, to_ts=1)


@patch("integrations.grafana.base.requests.get")
def test_discover_datasource_uids_propagates_http_error(mock_get: MagicMock) -> None:
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("401 Client Error")
    mock_get.return_value = response

    with pytest.raises(requests.HTTPError):
        _client().discover_datasource_uids()


@patch("integrations.grafana.base.requests.get")
def test_query_loki_label_values_propagates_http_error(mock_get: MagicMock) -> None:
    client = _client()
    client.loki_datasource_uid = "loki-uid"
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("503 Server Error")
    mock_get.return_value = response

    with pytest.raises(requests.HTTPError):
        client.query_loki_label_values("service_name")


def test_get_auth_headers_raises_when_token_missing() -> None:
    config = GrafanaAccountConfig(
        account_id="test",
        instance_url="https://grafana.example.com",
        read_token="",
    )
    client = GrafanaClientBase(config=config)
    with pytest.raises(ValueError, match="no API token"):
        client._get_auth_headers()
