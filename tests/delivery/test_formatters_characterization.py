"""Characterization tests: pin current Slack/Telegram renderer output.

These tests exist to prove the phase-B renderer refactor (issue #2007, task
``Refactor Slack + Telegram renderers behind sections``) doesn't change
observable behaviour. They are invariant-style — matching the codebase
style in ``test_report_provenance.py`` — but use ``string.index`` for
section ordering so any structural rearrangement during the refactor
trips the tests instead of slipping through.

When the refactor lands, every assertion here should still pass with no
edits. Tests that the refactor *intentionally* breaks (e.g. when Slack
gains a severity header) should be moved into a separate file or marked
with a comment naming the issue/PR that legitimised the change.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.delivery.publish_findings.formatters.report import (
    build_slack_blocks,
    format_slack_message,
    format_telegram_message,
)
from app.delivery.publish_findings.report_context import ReportContext, build_report_context


def _rich_state() -> dict[str, Any]:
    """A state exercising every section the current renderers can emit.

    Shape notes (see ``build_report_context`` for the full contract):

    - ``raw_alert`` carries the CloudWatch metadata and ``alert_id`` the
      renderers surface in their LINK and META sections.
    - Failed pods are read from ``evidence["datadog_failed_pods"]``, not
      from a top-level ``kube_failed_pods`` key.
    - ``investigation_duration_seconds`` is derived from
      ``investigation_started_at`` via ``time.monotonic()`` at runtime; tests
      patch ``ctx`` directly to keep the fixture deterministic.
    """
    return {
        "pipeline_name": "ingest",
        "alert_name": "PodCrashLooping",
        "severity": "critical",
        "raw_alert": {
            "cloudwatch_logs_url": (
                "https://us-east-1.console.aws.amazon.com/cloudwatch/home?#logsV2:log-groups"
            ),
            "cloudwatch_log_group": "/aws/lambda/ingest",
            "cloudwatch_region": "us-east-1",
            "alert_id": "alert-abc-123",
        },
        "root_cause": (
            "PodCrashLooping on ingest because the container exceeded its memory "
            "limit and was OOMKilled by the kubelet."
        ),
        "root_cause_category": "memory_exhaustion",
        "validity_score": 0.95,
        "validated_claims": [
            {
                "claim": "Pod was OOMKilled per kubelet logs",
                "evidence_sources": ["grafana_logs"],
            },
            {
                "claim": "Memory limit of 512Mi was reached",
                "evidence_sources": ["grafana_logs"],
            },
        ],
        "non_validated_claims": [
            {"claim": "Possibly related to a recent traffic spike"},
        ],
        "remediation_steps": [
            "Raise memory limit to 1Gi",
            "Add HPA based on memory utilization",
        ],
        "investigation_recommendations": [],
        "available_sources": {
            "grafana": {
                "grafana_endpoint": "https://acme.grafana.net",
                "service_name": "ingest-api",
                "pipeline_name": "ingest",
            },
            "eks": {
                "cluster_name": "prod-cluster",
                "namespace": "ingest",
                "region": "us-east-1",
            },
        },
        "evidence": {
            "grafana_error_logs": [
                {"message": "container ingest-api exited with code 137 (OOMKilled)"},
            ],
            "grafana_logs": [
                {"message": "memory limit reached: 512Mi/512Mi"},
            ],
            "cloudwatch_logs": [
                {"message": "[ingest-api] OOMKilled"},
            ],
            "datadog_failed_pods": [
                {
                    "pod_name": "ingest-api-7f9c-abc",
                    "namespace": "ingest",
                    "container": "ingest-api",
                    "exit_code": 137,
                    "memory_limit": "512Mi",
                }
            ],
        },
    }


@pytest.fixture
def rich_ctx() -> ReportContext:
    ctx = build_report_context(_rich_state())
    # `investigation_duration_seconds` is derived from time.monotonic() at
    # runtime — overwrite for deterministic META-section assertions.
    ctx["investigation_duration_seconds"] = 42
    return ctx


def _assert_order(text: str, markers: list[str]) -> None:
    """Assert each marker is present and appears in the given order."""
    last_index = -1
    for marker in markers:
        idx = text.find(marker)
        assert idx != -1, f"missing marker: {marker!r}\n--- output ---\n{text}"
        assert idx > last_index, (
            f"out-of-order: {marker!r} (at {idx}) should follow earlier section "
            f"(at {last_index})\n--- output ---\n{text}"
        )
        last_index = idx


# ---------------------------------------------------------------------------
# Telegram HTML — current parity target
# ---------------------------------------------------------------------------


def test_telegram_baseline_section_order(rich_ctx: ReportContext) -> None:
    body = format_telegram_message(rich_ctx)
    _assert_order(
        body,
        [
            "🔴",  # severity emoji starts the header
            "<b>PodCrashLooping</b>",  # alert name in header
            "<b>Findings</b>",
            "<b>Non-Validated Claims (Inferred)</b>",
            "<b>Provenance</b>",
            "<b>Recommended Actions</b>",
            "<b>Investigation Trace</b>",
            "<b>Cited Evidence</b>",
            "<b>CloudWatch</b>",
        ],
    )


def test_telegram_baseline_renders_html_not_slack_link_syntax(rich_ctx: ReportContext) -> None:
    """Telegram parse_mode=HTML must use <a href>, never Slack-style <url|label>."""
    body = format_telegram_message(rich_ctx)
    assert not re.search(r"<https?://[^|>]+\|", body), (
        f"Telegram output leaked Slack <url|label> syntax:\n{body}"
    )
    # ## markdown headers must not survive — Telegram doesn't render them
    assert "##" not in body


def test_telegram_baseline_includes_critical_content(rich_ctx: ReportContext) -> None:
    body = format_telegram_message(rich_ctx)
    assert "PodCrashLooping" in body
    assert "ingest" in body
    assert "Raise memory limit to 1Gi" in body
    assert "Pod was OOMKilled" in body
    assert "Possibly related" in body
    assert "exited with code 137" in body  # top_log appears
    # Meta block at the end
    assert "42s" in body
    assert "alert-abc-123" in body


# ---------------------------------------------------------------------------
# Slack mrkdwn — text fallback / terminal / ingest
# ---------------------------------------------------------------------------


def test_slack_text_baseline_section_order(rich_ctx: ReportContext) -> None:
    text = format_slack_message(rich_ctx)
    _assert_order(
        text,
        [
            "exited with code 137",  # top_log under root cause near the top
            "## Findings",
            "*Non-Validated Claims (Inferred):*",
            "*Provenance:*",
            "## Recommended Actions",
            "## Investigation Trace",
            "*Cited Evidence:*",
            "*Alert ID:*",
        ],
    )


def test_slack_text_baseline_includes_critical_content(rich_ctx: ReportContext) -> None:
    text = format_slack_message(rich_ctx)
    assert "PodCrashLooping" in text or "OOMKilled" in text  # root cause sentence
    assert "Pod was OOMKilled per kubelet logs" in text
    assert "Memory limit of 512Mi was reached" in text
    assert "Possibly related to a recent traffic spike" in text
    assert "Raise memory limit to 1Gi" in text
    assert "Timing: 42s" in text
    assert "alert-abc-123" in text


def test_slack_text_baseline_does_not_leak_html(rich_ctx: ReportContext) -> None:
    """Slack mrkdwn must not contain Telegram HTML tags."""
    text = format_slack_message(rich_ctx)
    assert "<b>" not in text
    assert "<a href" not in text
    assert "<code>" not in text


# ---------------------------------------------------------------------------
# Slack Block Kit — primary Slack rendering
# ---------------------------------------------------------------------------


def test_slack_blocks_baseline_header_order(rich_ctx: ReportContext) -> None:
    blocks = build_slack_blocks(rich_ctx)
    headers = [
        b["text"]["text"]
        for b in blocks
        if b.get("type") == "header" and b.get("text", {}).get("text")
    ]

    expected = [
        "Failed Pods",
        "Findings",
        "Provenance",
        "Recommended Actions",
        "Investigation Trace",
    ]
    last_index = -1
    for marker in expected:
        idx = next((i for i, h in enumerate(headers) if marker in h), -1)
        assert idx != -1, f"missing block header: {marker!r}\nheaders: {headers}"
        assert idx > last_index, f"out-of-order block header: {marker!r}\nheaders: {headers}"
        last_index = idx


def test_slack_blocks_baseline_first_block_is_severity_header(
    rich_ctx: ReportContext,
) -> None:
    """After issue/2007 task #7 the first block is the severity header.

    Before task #7 this asserted that the first block was the root-cause
    ``section`` — that's now the third block (after the severity ``header``
    and a ``context`` row carrying the severity tier + pipeline). See
    ``test_slack_blocks_baseline_root_cause_section_follows_severity_header``.
    """
    blocks = build_slack_blocks(rich_ctx)
    assert blocks, "expected at least one block"
    first = blocks[0]
    assert first["type"] == "header"
    assert "🔴" in first["text"]["text"]
    assert "PodCrashLooping" in first["text"]["text"]


def test_slack_blocks_baseline_root_cause_section_follows_severity_header(
    rich_ctx: ReportContext,
) -> None:
    """The first ``section`` block holds the root cause + top_log code span."""
    blocks = build_slack_blocks(rich_ctx)
    section_blocks = [b for b in blocks if b.get("type") == "section"]
    assert section_blocks, "expected at least one section block"
    first_section_text = section_blocks[0]["text"]["text"]
    assert "OOMKilled" in first_section_text or "memory" in first_section_text.lower()
    assert "`" in first_section_text


def test_slack_blocks_severity_context_row_after_header(rich_ctx: ReportContext) -> None:
    """The severity tier + pipeline live in a ``context`` block right under
    the ``header`` block (mirroring Telegram's two-line severity layout)."""
    blocks = build_slack_blocks(rich_ctx)
    assert blocks[1]["type"] == "context"
    elements = blocks[1]["elements"]
    text = elements[0]["text"]
    assert "CRITICAL" in text
    assert "ingest" in text  # pipeline name


def test_slack_text_baseline_starts_with_severity_header(rich_ctx: ReportContext) -> None:
    """Mrkdwn output leads with the emoji + alert + pipeline + severity lines."""
    text = format_slack_message(rich_ctx)
    assert text.startswith("🔴 *PodCrashLooping* · ingest\n_severity: CRITICAL_")


def test_slack_blocks_baseline_meta_context_last(rich_ctx: ReportContext) -> None:
    blocks = build_slack_blocks(rich_ctx)
    last = blocks[-1]
    assert last["type"] == "context"
    elements = last.get("elements") or []
    assert elements and elements[0].get("type") == "mrkdwn"
    text = elements[0]["text"]
    assert "Analyzed in 42s" in text
    assert "alert-abc-123" in text


def test_slack_blocks_baseline_respects_50_block_limit(rich_ctx: ReportContext) -> None:
    """Slack hard-limits messages to 50 blocks; current render is well under."""
    blocks = build_slack_blocks(rich_ctx)
    assert len(blocks) <= 50
