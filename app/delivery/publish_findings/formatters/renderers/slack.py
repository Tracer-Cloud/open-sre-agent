"""Slack renderer — walks the shared section list.

Two entry points:

- :func:`format_slack_message` — Slack mrkdwn text used as the ``text``
  fallback for notifications/accessibility, plus the terminal renderer
  and ingest payload.
- :func:`build_slack_blocks` — Slack Block Kit JSON used as the primary
  in-channel rendering.

Both consume :func:`prepare_sections_for` and never re-derive section
content from ctx. Helpers that are not yet section-aware (cited
evidence, cloudwatch link) still read ctx; they will be folded into
sections in a follow-up.

The Slack channel today does not surface a severity header — task #7 of
issue/2007 brings it to parity with Telegram. Until that lands, the
SEVERITY_HEADER section is silently skipped by both entry points.
"""

from __future__ import annotations

from typing import Any

from app.delivery.publish_findings.formatters.base import format_slack_link
from app.delivery.publish_findings.formatters.evidence import format_cited_evidence_section
from app.delivery.publish_findings.formatters.infrastructure import format_pod_line
from app.delivery.publish_findings.formatters.report import (
    _mrkdwn_section,
    _sanitize_for_slack,
    render_cloudwatch_link,
)
from app.delivery.publish_findings.formatters.sections import (
    Section,
    SectionKind,
    prepare_sections_for,
)
from app.delivery.publish_findings.formatters.severity import severity_display, severity_emoji
from app.delivery.publish_findings.report_context import ReportContext

# Slack Block Kit header blocks limit ``plain_text`` to 150 characters
# (https://api.slack.com/reference/block-kit/blocks#header). The severity
# emoji + alert name composes the header text, so an unusually long alert
# triggers truncation.
_HEADER_BLOCK_TEXT_LIMIT = 150

# ---------------------------------------------------------------------------
# mrkdwn text renderer
# ---------------------------------------------------------------------------


def format_slack_message(ctx: ReportContext) -> str:
    """Render the RCA report as Slack mrkdwn text.

    Used as the ``text`` fallback (notifications, accessibility, terminal,
    ingest) when Block Kit blocks are the primary rendered content.
    """
    parts: list[str] = []
    for section in prepare_sections_for(ctx):
        chunk = _render_text(section, ctx)
        if chunk:
            parts.append(chunk)
    return "\n\n".join(parts) + "\n"


def _render_text(section: Section, ctx: ReportContext) -> str:
    kind = section.kind
    if kind is SectionKind.SEVERITY_HEADER:
        return _text_severity_header(section)
    if kind is SectionKind.ROOT_CAUSE:
        return _text_root_cause(section)
    if kind is SectionKind.CLAIMS:
        return _text_claims(section)
    if kind is SectionKind.UPSTREAM_CORRELATION:
        return _text_correlation(section)
    if kind is SectionKind.PROVENANCE:
        return _text_provenance(section)
    if kind is SectionKind.REMEDIATION:
        return _text_remediation(section)
    if kind is SectionKind.TRACE:
        return _text_trace(section)
    if kind is SectionKind.EVIDENCE:
        return _sanitize_for_slack(format_cited_evidence_section(ctx)).strip()
    if kind is SectionKind.LINK:
        return render_cloudwatch_link(ctx).strip()
    if kind is SectionKind.META:
        return _text_meta(section)
    # FAILED_PODS not rendered in the mrkdwn fallback today.
    return ""


def _text_severity_header(section: Section) -> str:
    severity = str(section.extras.get("severity") or "")
    alert = str(section.extras.get("alert_name") or "Alert")
    pipeline = str(section.extras.get("pipeline_name") or "unknown")
    return (
        f"{severity_emoji(severity)} *{alert}* · {pipeline}\n"
        f"_severity: {severity_display(severity)}_"
    )


def _text_root_cause(section: Section) -> str:
    parts: list[str] = []
    if section.body:
        parts.append(section.body)
    top_log = section.extras.get("top_log")
    if top_log:
        parts.append(f"`{top_log}`")
    if not parts:
        # Match the legacy fallback when no sentence or log is available.
        return "Not determined (insufficient evidence)."
    return "\n".join(parts)


def _text_claims(section: Section) -> str:
    validated = section.extras.get("validated", False)
    bullets = _claim_bullets(section)
    if validated:
        return "## Findings\n" + "\n".join(bullets)
    return "*Non-Validated Claims (Inferred):*\n" + "\n".join(bullets)


def _text_provenance(section: Section) -> str:
    bullets = [f"• {_sanitize_for_slack(item)}" for item in section.items]
    return "*Provenance:*\n" + "\n".join(bullets)


def _text_correlation(section: Section) -> str:
    signals = section.extras.get("signals") or ()
    drivers = section.extras.get("drivers") or ()
    parts: list[str] = ["## Upstream Correlation"]
    if signals:
        parts.append("*Correlated signals:*\n" + "\n".join(f"• {s}" for s in signals))
    if drivers:
        parts.append("*Most likely causal drivers:*\n" + "\n".join(f"• {d}" for d in drivers))
    return "\n".join(parts)


def _text_remediation(section: Section) -> str:
    bullets = [f"• {_sanitize_for_slack(item)}" for item in section.items]
    return "## Recommended Actions\n" + "\n".join(bullets)


def _text_trace(section: Section) -> str:
    return "## Investigation Trace\n" + "\n".join(section.items)


def _text_meta(section: Section) -> str:
    bits: list[str] = []
    duration = section.extras.get("duration_seconds")
    alert_id = section.extras.get("alert_id")
    if duration is not None:
        bits.append(f"Timing: {duration}s")
    if alert_id:
        bits.append(f"*Alert ID:* {alert_id}")
    return "\n".join(bits)


# ---------------------------------------------------------------------------
# Block Kit renderer
# ---------------------------------------------------------------------------


def build_slack_blocks(ctx: ReportContext) -> list[dict]:
    """Build Slack Block Kit blocks for the RCA report.

    Produces a clean, well-structured message using Slack's native
    formatting: header, sections with mrkdwn, dividers, and context blocks.
    Truncates from the middle if the block count exceeds Slack's 50-block
    limit, preserving the first block (root cause) and the trailing meta.
    """
    blocks: list[dict[str, Any]] = []
    for section in prepare_sections_for(ctx):
        blocks.extend(_render_blocks(section, ctx))
    if len(blocks) > 50:
        blocks = blocks[:48] + blocks[-2:]
    return blocks


def _render_blocks(section: Section, ctx: ReportContext) -> list[dict[str, Any]]:
    kind = section.kind
    if kind is SectionKind.SEVERITY_HEADER:
        return _blocks_severity_header(section)
    if kind is SectionKind.ROOT_CAUSE:
        return _blocks_root_cause(section)
    if kind is SectionKind.FAILED_PODS:
        return _blocks_failed_pods(section)
    if kind is SectionKind.CLAIMS:
        return _blocks_claims(section)
    if kind is SectionKind.UPSTREAM_CORRELATION:
        return _blocks_correlation(section)
    if kind is SectionKind.PROVENANCE:
        return _blocks_provenance(section)
    if kind is SectionKind.REMEDIATION:
        return _blocks_remediation(section)
    if kind is SectionKind.TRACE:
        return _blocks_trace(section)
    if kind is SectionKind.EVIDENCE:
        return _blocks_evidence(ctx)
    if kind is SectionKind.LINK:
        return _blocks_link(ctx)
    if kind is SectionKind.META:
        return _blocks_meta(section)
    return []


def _blocks_severity_header(section: Section) -> list[dict[str, Any]]:
    """Severity header as a Block Kit ``header`` block plus a ``context`` row.

    The header block carries the severity emoji + alert name (the visual
    headline). The context block underneath shows the italic severity tier
    and pipeline name in smaller type, mirroring Telegram's two-line layout.
    """
    severity = str(section.extras.get("severity") or "")
    alert = str(section.extras.get("alert_name") or "Alert")
    pipeline = str(section.extras.get("pipeline_name") or "unknown")
    header_text = f"{severity_emoji(severity)} {alert}"
    if len(header_text) > _HEADER_BLOCK_TEXT_LIMIT:
        header_text = header_text[: _HEADER_BLOCK_TEXT_LIMIT - 1] + "…"
    context_text = f"_severity: {severity_display(severity)} · pipeline: {pipeline}_"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text, "emoji": True},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": context_text}],
        },
    ]


def _blocks_root_cause(section: Section) -> list[dict[str, Any]]:
    parts: list[str] = []
    parts.append(section.body or "Not determined (insufficient evidence)")
    top_log = section.extras.get("top_log")
    if top_log:
        parts.append(f"`{top_log}`")
    block = _mrkdwn_section("\n".join(parts))
    return [block] if block else []


def _blocks_failed_pods(section: Section) -> list[dict[str, Any]]:
    pods = section.extras.get("pods") or ()
    datadog_site = section.extras.get("datadog_site", "datadoghq.com")
    lines = [line for pod in pods[:5] if (line := format_pod_line(pod, datadog_site, bullet="• "))]
    if len(pods) > 5:
        lines.append(f"• ... and {len(pods) - 5} more pods")
    if not lines:
        return []
    section_block = _mrkdwn_section("\n".join(lines))
    if not section_block:
        return []
    return [
        {"type": "divider"},
        {"type": "header", "text": {"type": "plain_text", "text": "Failed Pods"}},
        section_block,
    ]


def _blocks_claims(section: Section) -> list[dict[str, Any]]:
    validated = section.extras.get("validated", False)
    bullets = _claim_bullets(section)
    if not bullets:
        return []
    if validated:
        section_block = _mrkdwn_section("\n".join(bullets))
        if not section_block:
            return []
        return [
            {"type": "divider"},
            {"type": "header", "text": {"type": "plain_text", "text": "Findings"}},
            section_block,
        ]
    # Non-validated: no header, just an inline-titled section block.
    section_block = _mrkdwn_section("*Inferred (not yet validated)*\n" + "\n".join(bullets))
    return [section_block] if section_block else []


def _blocks_correlation(section: Section) -> list[dict[str, Any]]:
    signals = section.extras.get("signals") or ()
    drivers = section.extras.get("drivers") or ()
    if not signals and not drivers:
        return []
    blocks: list[dict[str, Any]] = [
        {"type": "divider"},
        {"type": "header", "text": {"type": "plain_text", "text": "Upstream Correlation"}},
    ]
    if signals:
        signal_block = _mrkdwn_section(
            "*Correlated signals:*\n" + "\n".join(f"• {s}" for s in signals)
        )
        if signal_block:
            blocks.append(signal_block)
    if drivers:
        driver_block = _mrkdwn_section(
            "*Most likely causal drivers:*\n" + "\n".join(f"• {d}" for d in drivers)
        )
        if driver_block:
            blocks.append(driver_block)
    return blocks


def _blocks_provenance(section: Section) -> list[dict[str, Any]]:
    if not section.items:
        return []
    bullets = [f"• {_sanitize_for_slack(item)}" for item in section.items]
    section_block = _mrkdwn_section("\n".join(bullets))
    if not section_block:
        return []
    return [
        {"type": "divider"},
        {"type": "header", "text": {"type": "plain_text", "text": "Provenance"}},
        section_block,
    ]


def _blocks_remediation(section: Section) -> list[dict[str, Any]]:
    if not section.items:
        return []
    bullets = [f"• {_sanitize_for_slack(step)}" for step in section.items]
    section_block = _mrkdwn_section("\n".join(bullets))
    if not section_block:
        return []
    return [
        {"type": "divider"},
        {"type": "header", "text": {"type": "plain_text", "text": "Recommended Actions"}},
        section_block,
    ]


def _blocks_trace(section: Section) -> list[dict[str, Any]]:
    if not section.items:
        return []
    section_block = _mrkdwn_section("\n".join(section.items))
    if not section_block:
        return []
    return [
        {"type": "divider"},
        {"type": "header", "text": {"type": "plain_text", "text": "Investigation Trace"}},
        section_block,
    ]


def _blocks_evidence(ctx: ReportContext) -> list[dict[str, Any]]:
    text = format_cited_evidence_section(ctx).strip()
    if not text:
        return []
    block = _mrkdwn_section(text)
    if not block:
        return []
    return [{"type": "divider"}, block]


def _blocks_link(ctx: ReportContext) -> list[dict[str, Any]]:
    text = render_cloudwatch_link(ctx).strip()
    if not text:
        return []
    block = _mrkdwn_section(text)
    return [block] if block else []


def _blocks_meta(section: Section) -> list[dict[str, Any]]:
    bits: list[str] = []
    duration = section.extras.get("duration_seconds")
    alert_id = section.extras.get("alert_id")
    if duration is not None:
        bits.append(f"Analyzed in {duration}s")
    if alert_id:
        bits.append(f"Alert: {alert_id}")
    if not bits:
        return []
    return [
        {"type": "divider"},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": " | ".join(bits)}],
        },
    ]


# ---------------------------------------------------------------------------
# shared
# ---------------------------------------------------------------------------


def _claim_bullets(section: Section) -> list[str]:
    """Format a CLAIMS section's items + evidence refs as mrkdwn bullets."""
    evidence_refs = section.extras.get("evidence_refs") or ()
    bullets: list[str] = []
    for idx, claim_text in enumerate(section.items):
        sanitized = _sanitize_for_slack(claim_text)
        refs = evidence_refs[idx] if idx < len(evidence_refs) else ()
        evidence_part = ""
        if refs:
            labels = [
                format_slack_link(str(ref.get("display_id", "")), ref.get("url")) for ref in refs
            ]
            evidence_part = f" [{', '.join(labels)}]"
        bullets.append(f"• {sanitized}{evidence_part}")
    return bullets
