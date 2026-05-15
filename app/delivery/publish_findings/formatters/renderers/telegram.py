"""Telegram HTML renderer — walks the shared section list.

Telegram is invoked with ``parse_mode=HTML`` so the output uses ``<b>``,
``<i>``, ``<code>``, and ``<a href>`` tags. Section content that arrives
with Markdown-ish formatting (``*bold*``, ``**bold**``, fenced ``code``,
Slack-style ``<url|label>``) is normalised by ``_to_telegram_html_body``
before being escaped.

The Telegram channel currently does not render the ``FAILED_PODS`` section;
the per-pod information surfaces in the investigation trace instead.
"""

from __future__ import annotations

import html

from app.delivery.publish_findings.formatters.base import format_html_link
from app.delivery.publish_findings.formatters.evidence import format_cited_evidence_section_html
from app.delivery.publish_findings.formatters.report import (
    _sanitize_for_slack,
    _to_telegram_html_body,
    render_cloudwatch_link_html,
)
from app.delivery.publish_findings.formatters.sections import (
    Section,
    SectionKind,
    prepare_sections_for,
)
from app.delivery.publish_findings.formatters.severity import severity_display, severity_emoji
from app.delivery.publish_findings.report_context import ReportContext


def format_telegram_message(ctx: ReportContext) -> str:
    """Render the RCA report as Telegram HTML.

    Used with ``parse_mode=HTML`` by ``send_telegram_report``. Walks the
    section list from :func:`prepare_sections_for` and joins non-empty
    rendered chunks with blank-line separators.
    """
    parts: list[str] = []
    for section in prepare_sections_for(ctx):
        chunk = _render(section, ctx)
        if chunk:
            parts.append(chunk)
    return "\n\n".join(parts)


def _render(section: Section, ctx: ReportContext) -> str:
    kind = section.kind
    if kind is SectionKind.SEVERITY_HEADER:
        return _render_severity_header(section)
    if kind is SectionKind.ROOT_CAUSE:
        return _render_root_cause(section)
    if kind is SectionKind.CLAIMS:
        return _render_claims(section)
    if kind is SectionKind.UPSTREAM_CORRELATION:
        return _render_correlation(section)
    if kind is SectionKind.PROVENANCE:
        return _render_provenance(section)
    if kind is SectionKind.REMEDIATION:
        return _render_remediation(section)
    if kind is SectionKind.TRACE:
        return _render_trace(section)
    if kind is SectionKind.EVIDENCE:
        return format_cited_evidence_section_html(ctx).strip()
    if kind is SectionKind.LINK:
        return render_cloudwatch_link_html(ctx).strip()
    if kind is SectionKind.META:
        return _render_meta(section)
    # FAILED_PODS — currently not rendered for Telegram.
    return ""


def _render_severity_header(section: Section) -> str:
    severity = str(section.extras.get("severity") or "")
    alert = str(section.extras.get("alert_name") or "Alert")
    pipeline = str(section.extras.get("pipeline_name") or "unknown")
    return (
        f"{severity_emoji(severity)} <b>{html.escape(alert)}</b> · {html.escape(pipeline)}\n"
        f"<i>severity: {html.escape(severity_display(severity))}</i>"
    )


def _render_root_cause(section: Section) -> str:
    parts: list[str] = []
    if section.body:
        parts.append(_to_telegram_html_body(section.body))
    top_log = section.extras.get("top_log")
    if top_log:
        parts.append("<code>" + html.escape(str(top_log)) + "</code>")
    return "\n".join(parts)


def _render_claims(section: Section) -> str:
    title = section.title or ""
    evidence_refs = section.extras.get("evidence_refs") or ()
    bullets: list[str] = []
    for idx, claim_text in enumerate(section.items):
        body = _to_telegram_html_body(_sanitize_for_slack(claim_text))
        refs = evidence_refs[idx] if idx < len(evidence_refs) else ()
        evidence_part = ""
        if refs:
            labels = [
                format_html_link(str(ref.get("display_id", "")), ref.get("url")) for ref in refs
            ]
            evidence_part = f" [{', '.join(labels)}]"
        bullets.append(f"• {body}{evidence_part}")
    return f"<b>{html.escape(title)}</b>\n" + "\n".join(bullets)


def _render_provenance(section: Section) -> str:
    bullets = [f"• {_to_telegram_html_body(_sanitize_for_slack(item))}" for item in section.items]
    return "<b>Provenance</b>\n" + "\n".join(bullets)


def _render_correlation(section: Section) -> str:
    signals = section.extras.get("signals") or ()
    drivers = section.extras.get("drivers") or ()
    parts: list[str] = ["<b>Upstream Correlation</b>"]
    if signals:
        body = "\n".join(f"• {_to_telegram_html_body(_sanitize_for_slack(s))}" for s in signals)
        parts.append("<i>Correlated signals:</i>\n" + body)
    if drivers:
        body = "\n".join(f"• {_to_telegram_html_body(_sanitize_for_slack(d))}" for d in drivers)
        parts.append("<i>Most likely causal drivers:</i>\n" + body)
    return "\n".join(parts)


def _render_remediation(section: Section) -> str:
    bullets = [f"• {_to_telegram_html_body(_sanitize_for_slack(item))}" for item in section.items]
    return "<b>Recommended Actions</b>\n" + "\n".join(bullets)


def _render_trace(section: Section) -> str:
    bullets = [_to_telegram_html_body(item) for item in section.items]
    return "<b>Investigation Trace</b>\n" + "\n".join(bullets)


def _render_meta(section: Section) -> str:
    bits: list[str] = []
    duration = section.extras.get("duration_seconds")
    alert_id = section.extras.get("alert_id")
    if duration is not None:
        bits.append(f"Timing: {duration}s")
    if alert_id:
        bits.append(f"Alert ID: {alert_id}")
    if not bits:
        return ""
    return "<i>" + html.escape(" | ".join(bits)) + "</i>"
