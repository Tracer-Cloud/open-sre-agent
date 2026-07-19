"""Tests for the Grafana log sink."""

import json
from typing import Any
from unittest.mock import patch

import pytest
from requests import RequestException

from integrations.grafana.base import GrafanaClientBase
from integrations.grafana.config import GrafanaAccountConfig
from integrations.grafana.log_sink import GrafanaLogSink, GrafanaLogSinkConfig


class _FakeGrafanaClient(GrafanaClientBase):
    """Stand-in for GrafanaClientBase that records API calls."""

    def __init__(self, *, annotation_result: dict[str, Any] | None = None) -> None:
        super().__init__(
            GrafanaAccountConfig(
                account_id="test-account",
                instance_url="https://grafana.example.com",
                read_token="test-token",
            )
        )
        self.annotation_calls: list[dict[str, Any]] = []
        self._annotation_result = annotation_result or {"success": True, "id": 1}

    def create_annotation(self, text: str, tags: list[str], **kwargs: Any) -> dict[str, Any]:
        self.annotation_calls.append({"text": text, "tags": tags, **kwargs})
        return self._annotation_result


def _sample_state():
    return {
        "alert_name": "Payments latency alert",
        "pipeline_name": "payments-pipeline",
        "severity": "critical",
        "root_cause": "db slowness",
        "remediation_steps": ["restart DB"],
        "evidence": [
            {"type": "log", "content": "db error log"},
        ],
        "correlation": {"cid": "123"},
        "run_id": "run-1",
        "alert_source": "pagerduty",
    }


@patch("integrations.grafana.log_sink.requests.post")
def test_loki_push_success(mock_post):
    mock_post.return_value.status_code = 204
    client = _FakeGrafanaClient()
    config = GrafanaLogSinkConfig(
        push_to_loki=True,
        loki_push_url="https://loki.example.com",
        loki_write_token="token",
    )
    sink = GrafanaLogSink(client, config=config)

    state = _sample_state()
    messages = {"slack_text": "summary"}
    result = sink.send_investigation_report(state, messages=messages)

    # --- basic call checks ---
    assert result is True
    mock_post.assert_called_once()

    call_args = mock_post.call_args
    assert call_args[0][0] == "https://loki.example.com/loki/api/v1/push"

    headers = call_args.kwargs["headers"]
    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer token"

    # --- payload shape checks ---
    payload = call_args.kwargs["json"]
    assert "streams" in payload
    assert isinstance(payload["streams"], list)
    assert len(payload["streams"]) == 1

    entry = payload["streams"][0]
    assert "stream" in entry
    assert "values" in entry

    # stream keys
    stream = entry["stream"]
    assert stream["alert_source"] == "pagerduty"
    assert stream["severity"] == "critical"
    assert stream["job"] == "opensre"
    assert stream["source"] == "investigation"

    # values shape
    values = entry["values"]
    assert isinstance(values, list)
    assert len(values) == 1
    ts, payload_str = values[0]
    assert ts.isdigit()

    # inner JSON payload
    inner = json.loads(payload_str)
    assert inner["alert_name"] == "Payments latency alert"
    assert inner["pipeline_name"] == "payments-pipeline"
    assert inner["root_cause"] == "db slowness"
    assert inner["remediation_steps"] == ["restart DB"]
    assert inner["evidence_count"] == 1
    assert inner["correlation"]["cid"] == "123"
    assert inner["report_summary"] == "summary"
    assert inner["run_id"] == "run-1"


@patch("integrations.grafana.log_sink.requests.post")
def test_loki_push_defaults_verify_true_without_client(mock_post):
    """Loki-only mode (no Grafana client) has no ssl_verify source; defaults to True."""
    mock_post.return_value.status_code = 204
    sink = GrafanaLogSink(
        client=None,
        config=GrafanaLogSinkConfig(loki_push_url="https://loki.example.com"),
    )
    sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"})
    assert mock_post.call_args.kwargs["verify"] is True


@patch("integrations.grafana.log_sink.requests.post")
def test_loki_push_honors_client_ssl_verify(mock_post):
    """When a Grafana client is configured, Loki push must honor its ssl_verify

    (e.g. self-signed on-prem certs with verify_ssl=False), not silently
    default to True like a bare ``requests.post`` call.
    """
    mock_post.return_value.status_code = 204
    client = GrafanaClientBase(
        GrafanaAccountConfig(
            account_id="test-account",
            instance_url="https://grafana.internal",
            read_token="test-token",
            verify_ssl=False,
        )
    )
    sink = GrafanaLogSink(
        client,
        config=GrafanaLogSinkConfig(
            push_to_loki=True,
            create_annotations=False,
            loki_push_url="https://loki.example.com",
        ),
    )
    sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"})
    assert mock_post.call_args.kwargs["verify"] is False


@patch("integrations.grafana.log_sink.resolve_env_credential")
def test_loki_write_token_resolves_env_then_keyring(mock_resolve, monkeypatch):
    """GRAFANA_WRITE_TOKEN is a *_TOKEN secret; must go through the credential
    resolution helper (env then keyring), not a bare ``os.getenv``.
    """
    monkeypatch.delenv("GRAFANA_WRITE_TOKEN", raising=False)
    mock_resolve.return_value = "keyring-token"
    sink = GrafanaLogSink(config=GrafanaLogSinkConfig())
    mock_resolve.assert_called_once_with("GRAFANA_WRITE_TOKEN")
    assert sink._loki_write_token == "keyring-token"


@patch("integrations.grafana.log_sink.requests.post")
def test_loki_push_no_url_skips(mock_post, monkeypatch):
    monkeypatch.delenv("GRAFANA_LOKI_PUSH_URL", raising=False)
    sink = GrafanaLogSink(
        _FakeGrafanaClient(),
        config=GrafanaLogSinkConfig(push_to_loki=True, create_annotations=False),
    )
    assert (
        sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"}) is False
    )
    mock_post.assert_not_called()


@patch("integrations.grafana.log_sink.requests.post")
def test_loki_push_failure_swallowed(mock_post):
    mock_post.side_effect = RequestException()
    sink = GrafanaLogSink(
        _FakeGrafanaClient(),
        config=GrafanaLogSinkConfig(
            push_to_loki=True,
            create_annotations=False,
            loki_push_url="https://loki.example.com",
        ),
    )
    assert (
        sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"}) is False
    )


@pytest.mark.parametrize(
    "cfg_kwargs, expected_result, expected_post_calls, expected_annotation_calls",
    [
        ({"push_to_loki": False, "create_annotations": True}, True, 0, 1),
        (
            {
                "push_to_loki": True,
                "create_annotations": False,
                "loki_push_url": "https://loki.example.com",
            },
            True,
            1,
            0,
        ),
        ({"push_to_loki": False, "create_annotations": False}, False, 0, 0),
    ],
)
@patch("integrations.grafana.log_sink.requests.post")
def test_channel_flags_disable_expected_path(
    mock_post, cfg_kwargs, expected_result, expected_post_calls, expected_annotation_calls
):
    mock_post.return_value.status_code = 204
    client = _FakeGrafanaClient()
    sink = GrafanaLogSink(client, config=GrafanaLogSinkConfig(**cfg_kwargs))

    result = sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"})

    assert result is expected_result
    assert mock_post.call_count == expected_post_calls
    assert len(client.annotation_calls) == expected_annotation_calls


def test_annotation_create_success_text_and_tags():
    client = _FakeGrafanaClient()
    sink = GrafanaLogSink(
        client,
        config=GrafanaLogSinkConfig(push_to_loki=False, create_annotations=True),
    )

    assert (
        sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"}) is True
    )
    call = client.annotation_calls[0]

    assert "🔴 **OpenSRE Investigation**: Payments latency alert" in call["text"]
    assert "**Severity**: CRITICAL" in call["text"]
    assert "**Root cause**: db slowness" in call["text"]
    assert call["tags"] == ["opensre", "investigation", "critical", "pagerduty"]


def test_annotation_failure_swallowed():
    client = _FakeGrafanaClient(annotation_result={"success": False})
    sink = GrafanaLogSink(
        client,
        config=GrafanaLogSinkConfig(push_to_loki=False, create_annotations=True),
    )
    assert (
        sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"}) is False
    )
    assert len(client.annotation_calls) == 1


@pytest.mark.parametrize(
    "annotation_result, expected",
    [({"success": True, "id": 1}, True), ({"success": False}, False)],
)
@patch("integrations.grafana.log_sink.requests.post")
def test_send_report_outcome_matrix(mock_post, annotation_result, expected):
    mock_post.side_effect = RequestException("loki down")
    sink = GrafanaLogSink(
        _FakeGrafanaClient(annotation_result=annotation_result),
        config=GrafanaLogSinkConfig(loki_push_url="https://loki.example.com"),
    )
    assert (
        sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"})
        is expected
    )


@patch("integrations.grafana.log_sink.requests.post")
def test_empty_state_no_crash(mock_post):
    mock_post.return_value.status_code = 204
    sink = GrafanaLogSink(
        _FakeGrafanaClient(),
        config=GrafanaLogSinkConfig(
            loki_push_url="https://loki.example.com",
            create_annotations=False,
        ),
    )

    assert sink.send_investigation_report({}, messages={}) is True
    stream = mock_post.call_args.kwargs["json"]["streams"][0]
    assert stream["stream"]["severity"] == "unknown"
    assert stream["stream"]["alert_source"] == "unknown"

    _, line = stream["values"][0]
    payload = json.loads(line)
    assert payload["alert_name"] == ""
    assert payload["run_id"] == ""


@patch("integrations.grafana.log_sink.requests.post")
def test_loki_push_works_without_client(mock_post):
    mock_post.return_value.status_code = 204
    sink = GrafanaLogSink(
        client=None,
        config=GrafanaLogSinkConfig(
            loki_push_url="https://loki.example.com",
            create_annotations=False,
        ),
    )
    assert (
        sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"}) is True
    )


@patch("integrations.grafana.log_sink.requests.post")
def test_annotation_skipped_without_client(mock_post):
    mock_post.return_value.status_code = 204
    sink = GrafanaLogSink(
        client=None,
        config=GrafanaLogSinkConfig(
            create_annotations=True,
            push_to_loki=False,
        ),
    )
    assert (
        sink.send_investigation_report(_sample_state(), messages={"slack_text": "summary"}) is False
    )


def test_loki_only_config_skips_annotations_without_client():
    sink = GrafanaLogSink(
        client=None,
        config=GrafanaLogSinkConfig(
            loki_push_url="https://loki.example.com",
            push_to_loki=True,
            create_annotations=False,
        ),
    )
    assert sink._config.create_annotations is False
