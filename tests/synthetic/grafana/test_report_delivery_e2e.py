"""Synthetic end-to-end test: report bootstrap → dispatch → Grafana log sink.

Exercises the platform delivery registry bootstrap, the vendor-neutral
``dispatch_report`` loop, and the Grafana adapter's Loki push path without
requiring a live Grafana instance or investigation tool calls.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.state import AgentState
from platform.reporting.delivery_registry import get_delivery_adapter
from tools.investigation.reporting.delivery.bootstrap import ensure_delivery_adapters_registered
from tools.investigation.reporting.delivery.dispatch import dispatch_report
from tools.investigation.reporting.formatters.messages import ReportMessages

pytestmark = pytest.mark.synthetic


def _minimal_state(**overrides: Any) -> AgentState:
    base: AgentState = {
        "resolved_integrations": {},
        "alert_name": "Synthetic checkout 502",
        "severity": "critical",
        "root_cause": "upstream timeout",
        "remediation_steps": ["rollback"],
        "evidence": {"logs": "error 502"},
        "correlation": {},
        "run_id": "synthetic-run-1",
        "alert_source": "synthetic",
    }
    base.update(overrides)
    return base


def _messages() -> ReportMessages:
    return ReportMessages(
        slack_text="Synthetic investigation summary",
        telegram_html="Synthetic investigation summary",
        whatsapp_text="Synthetic investigation summary",
        slack_blocks=[],
    )


def test_bootstrap_registers_grafana_adapter() -> None:
    ensure_delivery_adapters_registered()
    adapter = get_delivery_adapter("grafana")
    assert adapter is not None
    assert adapter.name == "grafana"


@patch("integrations.grafana.log_sink.requests.post")
@patch("integrations.slack.delivery.send_slack_report", return_value=(False, None))
@patch("integrations.slack.delivery.build_action_blocks", return_value=[])
def test_dispatch_report_loki_only_e2e(
    _mock_blocks: MagicMock,
    _mock_slack: MagicMock,
    mock_post: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_delivery_adapters_registered()
    monkeypatch.setenv("GRAFANA_LOKI_PUSH_URL", "https://loki.test")
    monkeypatch.setenv("GRAFANA_WRITE_TOKEN", "write-tok")
    mock_post.return_value.status_code = 204

    dispatch_report(
        _minimal_state(),
        _messages(),
        investigation_id="inv-1",
        investigation_url="https://app.example.com/inv/1",
    )

    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert call_args.args[0] == "https://loki.test/loki/api/v1/push"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer write-tok"

    stream = call_args.kwargs["json"]["streams"][0]
    assert stream["stream"]["severity"] == "critical"
    assert stream["stream"]["alert_source"] == "synthetic"
    payload = json.loads(stream["values"][0][1])
    assert payload["alert_name"] == "Synthetic checkout 502"
    assert payload["root_cause"] == "upstream timeout"


@patch("integrations.grafana.log_sink.requests.post")
@patch("integrations.slack.delivery.send_slack_report", return_value=(False, None))
@patch("integrations.slack.delivery.build_action_blocks", return_value=[])
def test_dispatch_report_grafana_failure_does_not_abort_dispatch(
    _mock_blocks: MagicMock,
    _mock_slack: MagicMock,
    mock_post: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from requests import RequestException

    ensure_delivery_adapters_registered()
    monkeypatch.setenv("GRAFANA_LOKI_PUSH_URL", "https://loki.test")
    mock_post.side_effect = RequestException("loki down")

    blocks = dispatch_report(
        _minimal_state(),
        _messages(),
        investigation_id=None,
        investigation_url=None,
    )

    assert isinstance(blocks, list)
    mock_post.assert_called_once()


class _FakeAnnotationClient:
    """Minimal Grafana client stand-in for annotation delivery."""

    def __init__(self) -> None:
        self.annotation_calls: list[dict[str, Any]] = []

    def create_annotation(self, text: str, tags: list[str], **kwargs: Any) -> dict[str, Any]:
        self.annotation_calls.append({"text": text, "tags": tags, **kwargs})
        return {"success": True, "id": 42}


@patch("integrations.grafana.log_sink.requests.post")
@patch("integrations.slack.delivery.send_slack_report", return_value=(False, None))
@patch("integrations.slack.delivery.build_action_blocks", return_value=[])
def test_dispatch_report_grafana_annotations_only_e2e(
    _mock_blocks: MagicMock,
    _mock_slack: MagicMock,
    mock_post: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grafana integration with endpoint -> annotations; no Loki env -> no push."""
    ensure_delivery_adapters_registered()
    monkeypatch.delenv("GRAFANA_LOKI_PUSH_URL", raising=False)
    monkeypatch.delenv("GRAFANA_WRITE_TOKEN", raising=False)

    fake_client = _FakeAnnotationClient()
    monkeypatch.setattr(
        "integrations.grafana.client.get_grafana_client_from_credentials",
        lambda **_kwargs: fake_client,
    )

    dispatch_report(
        _minimal_state(
            resolved_integrations={
                "grafana": {"endpoint": "https://grafana.example", "api_key": "k"}
            }
        ),
        _messages(),
        investigation_id="inv-2",
        investigation_url="https://app.example.com/inv/2",
    )

    mock_post.assert_not_called()
    assert len(fake_client.annotation_calls) == 1
    call = fake_client.annotation_calls[0]
    assert "Synthetic checkout 502" in call["text"]
    assert "critical" in call["tags"]
    assert "synthetic" in call["tags"]


@patch("integrations.grafana.log_sink.requests.post")
@patch("integrations.slack.delivery.send_slack_report", return_value=(False, None))
@patch("integrations.slack.delivery.build_action_blocks", return_value=[])
def test_dispatch_report_grafana_and_loki_e2e(
    _mock_blocks: MagicMock,
    _mock_slack: MagicMock,
    mock_post: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete delivery flow: Grafana annotations + Loki push in one dispatch."""
    ensure_delivery_adapters_registered()
    monkeypatch.setenv("GRAFANA_LOKI_PUSH_URL", "https://loki.test")
    monkeypatch.setenv("GRAFANA_WRITE_TOKEN", "write-tok")
    mock_post.return_value.status_code = 204

    fake_client = _FakeAnnotationClient()
    monkeypatch.setattr(
        "integrations.grafana.client.get_grafana_client_from_credentials",
        lambda **_kwargs: fake_client,
    )

    dispatch_report(
        _minimal_state(
            resolved_integrations={
                "grafana": {"endpoint": "https://grafana.example", "api_key": "k"}
            }
        ),
        _messages(),
        investigation_id="inv-3",
        investigation_url="https://app.example.com/inv/3",
    )

    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "https://loki.test/loki/api/v1/push"
    assert len(fake_client.annotation_calls) == 1
    assert "Synthetic checkout 502" in fake_client.annotation_calls[0]["text"]
