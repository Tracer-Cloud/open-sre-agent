"""WhatsApp and SMS are plain-text channels: no Slack link markup may reach them."""

from __future__ import annotations

from tools.investigation.reporting.context import build_report_context
from tools.investigation.reporting.formatters.messages import build_report_messages
from tools.investigation.reporting.formatters.report import format_whatsapp_message


def _make_state() -> dict:
    return {
        "alert_name": "Checkout latency spike",
        "severity": "critical",
        "root_cause": "Checkout service was throttled by the upstream API cluster.",
        "validated_claims": [
            {
                "claim": "Grafana logs show repeated 500 responses.",
                "evidence_sources": ["grafana_logs"],
            }
        ],
        "available_sources": {
            "grafana": {
                "grafana_endpoint": "https://myorg.grafana.net",
                "service_name": "checkout-api",
            },
        },
        "evidence": {
            "grafana_logs": [{"message": "service unavailable"}],
            "grafana_logs_query": '{service="checkout-api"}',
        },
    }


def test_whatsapp_message_renders_evidence_links_as_plain_text() -> None:
    body = format_whatsapp_message(build_report_context(_make_state()))

    assert "<https://" not in body
    assert "E1 (https://myorg.grafana.net" in body


def test_whatsapp_message_renders_s3_trace_links_as_plain_text() -> None:
    state = _make_state()
    state["raw_alert"] = {
        "annotations": {
            "landing_bucket": "tracer-landing",
            "s3_key": "runs/checkout/input.json",
        },
    }

    body = format_whatsapp_message(build_report_context(state))

    assert "<https://" not in body
    assert "S3 object (https://" in body


def test_sms_body_carries_no_slack_link_markup() -> None:
    messages = build_report_messages(build_report_context(_make_state()))

    assert messages.sms_text == messages.whatsapp_text
    assert "<https://" not in messages.sms_text


def test_slack_and_telegram_keep_their_own_link_syntax() -> None:
    """The plain-text conversion must not bleed into the other channels."""
    messages = build_report_messages(build_report_context(_make_state()))

    assert "<https://myorg.grafana.net" in messages.slack_text
    assert '<a href="https://myorg.grafana.net' in messages.telegram_html
