"""Tests for GrafanaClientBase query methods surfacing API errors (#2944).

The client must let an API failure (auth, network, malformed body) propagate
instead of returning an empty collection, so callers can tell a real "no
results" apart from a failure and not report a broken system as healthy.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from integrations.grafana.base import GrafanaClientBase
from integrations.grafana.config import GrafanaAccountConfig


def _client(**overrides: object) -> GrafanaClientBase:
    config = GrafanaAccountConfig(
        account_id="acct",
        instance_url="https://grafana.example.com",
        read_token="glsa_test",
        **overrides,
    )
    return GrafanaClientBase(config)


def _failing_response() -> MagicMock:
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError(response=MagicMock(status_code=401))
    return response


def _ok_response(payload: object) -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


class TestQueryAlertRulesErrors:
    def test_http_error_propagates(self) -> None:
        with (
            patch("integrations.grafana.base.requests.get", return_value=_failing_response()),
            pytest.raises(requests.HTTPError),
        ):
            _client().query_alert_rules()

    def test_empty_response_still_returns_empty(self) -> None:
        # A genuine empty result must stay empty — only failures should raise.
        with patch("integrations.grafana.base.requests.get", return_value=_ok_response({})):
            assert _client().query_alert_rules() == []


class TestQueryAnnotationsErrors:
    def test_http_error_propagates(self) -> None:
        with (
            patch("integrations.grafana.base.requests.get", return_value=_failing_response()),
            pytest.raises(requests.HTTPError),
        ):
            _client().query_annotations(from_ts=0, to_ts=1)

    def test_empty_response_still_returns_empty(self) -> None:
        with patch("integrations.grafana.base.requests.get", return_value=_ok_response([])):
            assert _client().query_annotations(from_ts=0, to_ts=1) == []


class TestQueryLokiLabelValuesErrors:
    def test_http_error_propagates(self) -> None:
        client = _client(loki_datasource_uid="loki-uid")
        with (
            patch("integrations.grafana.base.requests.get", return_value=_failing_response()),
            pytest.raises(requests.HTTPError),
        ):
            client.query_loki_label_values("service_name")

    def test_empty_response_still_returns_empty(self) -> None:
        client = _client(loki_datasource_uid="loki-uid")
        with patch(
            "integrations.grafana.base.requests.get",
            return_value=_ok_response({"data": []}),
        ):
            assert client.query_loki_label_values("service_name") == []


class TestDiscoverDatasourceUidsStaysBestEffort:
    def test_api_failure_degrades_to_empty(self) -> None:
        # Discovery runs during client construction, so a failure must not raise
        # (it would break clients that pass explicit UIDs) — it degrades to {}.
        with patch("integrations.grafana.base.requests.get", return_value=_failing_response()):
            assert _client().discover_datasource_uids() == {}
