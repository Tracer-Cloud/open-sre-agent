"""Shared helpers + back-compat re-exports for the RCA report formatters.

The public entry points ``format_slack_message``, ``build_slack_blocks``,
and ``format_telegram_message`` now live in
``app.delivery.publish_findings.formatters.renderers.{slack,telegram}``;
they are re-exported at the bottom of this module so existing imports
(notably ``app.delivery.publish_findings.node``) continue to work.

This module still owns the derivation and channel-syntax helpers that
both renderers and ``sections.build_sections`` depend on:

- Root-cause derivation: :func:`_derive_root_cause_sentence`,
  :func:`_get_top_error_log`.
- Slack mrkdwn ↔ Telegram HTML conversion: :func:`_sanitize_for_slack`,
  :func:`_to_telegram_html_body`, :func:`_mrkdwn_section`.
- Evidence/citation glue: :func:`_resolve_evidence_tags`,
  :func:`_format_provenance_lines`.
- CloudWatch links: :func:`render_cloudwatch_link`,
  :func:`render_cloudwatch_link_html`.

These helpers stay here for now to keep the renderer refactor diff
narrow; a follow-up will collapse them into ``sections.py`` (or a
sibling ``derive.py``) so the renderers stop importing from this module.
"""

import html
import re

from app.delivery.publish_findings.formatters.base import format_html_link, format_slack_link
from app.delivery.publish_findings.report_context import ReportContext
from app.delivery.publish_findings.urls.aws import build_cloudwatch_url


def render_cloudwatch_link(ctx: ReportContext) -> str:
    """Render CloudWatch logs link if available in context."""
    cw_url = ctx.get("cloudwatch_logs_url")
    cw_group = ctx.get("cloudwatch_log_group")
    cw_stream = ctx.get("cloudwatch_log_stream")

    if cw_url:
        return f"\n*{format_slack_link('CloudWatch Logs', cw_url)}*\n"
    elif cw_group and cw_stream:
        url = build_cloudwatch_url(ctx)
        view_link = format_slack_link("CloudWatch Logs", url) if url else None
        if view_link:
            return f"\n*{view_link}*\n"
        return f"\n*CloudWatch Logs:*\n* Log Group: {cw_group}\n* Log Stream: {cw_stream}\n"

    return ""


def _format_provenance_lines(ctx: ReportContext) -> list[str]:
    provenance = ctx.get("source_provenance") or {}
    lines: list[str] = []
    for source_name, entry in provenance.items():
        label = entry.get("label") or source_name.title()
        summary = entry.get("summary") or ""
        if summary:
            lines.append(f"• {label}: {summary}")
    return lines


# ---------------------------------------------------------------------------
# Shared section helpers — called by both text and block renderers
# ---------------------------------------------------------------------------


def _render_claim_lines(ctx: ReportContext) -> tuple[list[str], list[str]]:
    """Return (validated_lines, non_validated_lines) as plain mrkdwn bullet strings.

    Each validated line includes evidence citations like [E1, E2]. Both renderers
    (format_slack_message and build_slack_blocks) call this to avoid duplicating
    the catalog-lookup and link-formatting logic.
    """
    catalog = ctx.get("evidence_catalog") or {}
    evidence = ctx.get("evidence") or {}

    validated_lines: list[str] = []
    for claim_data in ctx.get("validated_claims", []):
        claim = claim_data.get("claim", "")
        claim = _resolve_evidence_tags(claim, evidence)
        claim = _sanitize_for_slack(claim)
        evidence_ids = claim_data.get("evidence_ids", [])
        evidence_labels = claim_data.get("evidence_labels", [])
        evidence_list: list[str] = []
        if evidence_ids:
            for eid in evidence_ids:
                entry = catalog.get(eid, {})
                disp = entry.get("display_id", eid)
                url = entry.get("url")
                evidence_list.append(format_slack_link(disp, url) if url else disp)
        elif evidence_labels:
            evidence_list = list(evidence_labels)
        ev_str = f" [{', '.join(evidence_list)}]" if evidence_list else ""
        validated_lines.append(f"\u2022 {claim}{ev_str}")

    non_validated_lines: list[str] = [
        f"\u2022 {_sanitize_for_slack(cd.get('claim', ''))}"
        for cd in ctx.get("non_validated_claims", [])
    ]

    return validated_lines, non_validated_lines


def _sanitize_for_slack(text: str) -> str:
    """Convert markdown formatting to Slack mrkdwn.

    Slack does not render # headers, ** bold, or other standard markdown.
    This converts common patterns to Slack-native formatting.
    """
    result = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)
    return result


_SLACK_LINK_RE = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")


def _star_pairs_to_bold_placeholders(line: str, bold_ph: dict[str, str]) -> str:
    """Replace only paired ``*inner*`` spans (inner has no ``*``); lone ``*`` stay literal."""
    out = line
    while True:
        m = re.search(r"\*([^*\n]+)\*", out)
        if not m:
            break
        tok = f"«B{len(bold_ph)}»"
        bold_ph[tok] = "<b>" + html.escape(m.group(1)) + "</b>"
        out = out[: m.start()] + tok + out[m.end() :]
    return out


def _to_telegram_html_body(text: str) -> str:
    """Convert mixed Slack-style text (headers, *bold*, `code`, <url|label>) to Telegram HTML."""
    placeholders: dict[str, str] = {}

    def _put(chunk: str) -> str:
        token = f"«{len(placeholders)}»"
        placeholders[token] = chunk
        return token

    s = text
    s = re.sub(r"`([^`]+)`", lambda m: _put("<code>" + html.escape(m.group(1)) + "</code>"), s)
    s = _SLACK_LINK_RE.sub(
        lambda m: _put(format_html_link(m.group(2) or m.group(1), m.group(1))),
        s,
    )

    out_lines: list[str] = []
    for line in s.splitlines():
        hdr = re.match(r"^#{1,6}\s+(.+)$", line)
        if hdr:
            out_lines.append("<b>" + html.escape(hdr.group(1).strip()) + "</b>")
            continue
        bold_ph: dict[str, str] = {}
        starred = _star_pairs_to_bold_placeholders(line, bold_ph)
        escaped = html.escape(starred)
        for token, inner in sorted(bold_ph.items(), key=lambda kv: -len(kv[0])):
            escaped = escaped.replace(token, inner)
        out_lines.append(escaped)

    merged = "\n".join(out_lines)
    for token, chunk in sorted(placeholders.items(), key=lambda kv: -len(kv[0])):
        merged = merged.replace(token, chunk)
    return merged


def _norm_banner_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _telegram_baseline_repeats_header(ctx: ReportContext, root_cause_sentence: str) -> bool:
    """True when the derived root-cause line only repeats alert metadata already in the header."""
    alert = (ctx.get("alert_name") or "").strip()
    pipeline = (ctx.get("pipeline_name") or "").strip()
    if not alert or not pipeline:
        return False
    s = root_cause_sentence.strip()
    if len(s) > 220:
        return False
    rc = _norm_banner_key(s)
    if _norm_banner_key(alert) not in rc or _norm_banner_key(pipeline) not in rc:
        return False
    if "because" in rc or "due to" in rc or "caused" in rc:
        return False
    if "severity" in rc:
        return True
    return len(s) < 120


def _severity_telegram_header(ctx: ReportContext) -> str:
    """Severity emoji row aligned with Hermes Telegram sink conventions."""
    raw = (ctx.get("severity") or "").strip()
    lower = raw.lower()
    emoji = {
        "critical": "🔴",
        "crit": "🔴",
        "high": "🟠",
        "error": "🟠",
        "medium": "🟡",
        "warning": "🟡",
        "warn": "🟡",
        "low": "🟢",
        "info": "🟢",
        "none": "⚪",
        "healthy": "🟢",
        "normal": "🟢",
    }.get(lower, "⚠️")
    display_sev = raw.upper() if raw else "UNKNOWN"
    alert = html.escape(str(ctx.get("alert_name") or "Alert"))
    pipeline = html.escape(str(ctx.get("pipeline_name") or "unknown"))
    return f"{emoji} <b>{alert}</b> · {pipeline}\n<i>severity: {html.escape(display_sev)}</i>"


def _render_claim_lines_telegram(ctx: ReportContext) -> tuple[list[str], list[str]]:
    catalog = ctx.get("evidence_catalog") or {}
    evidence = ctx.get("evidence") or {}

    validated_lines: list[str] = []
    for claim_data in ctx.get("validated_claims", []):
        claim = claim_data.get("claim", "")
        claim = _resolve_evidence_tags(claim, evidence)
        claim = _sanitize_for_slack(claim)
        evidence_ids = claim_data.get("evidence_ids", [])
        evidence_labels = claim_data.get("evidence_labels", [])
        evidence_list: list[str] = []
        if evidence_ids:
            for eid in evidence_ids:
                entry = catalog.get(eid, {})
                disp = entry.get("display_id", eid)
                url = entry.get("url")
                evidence_list.append(format_html_link(str(disp), url or None))
        elif evidence_labels:
            evidence_list = [html.escape(str(x)) for x in evidence_labels]
        ev_str = f" [{', '.join(evidence_list)}]" if evidence_list else ""
        validated_lines.append(f"• {_to_telegram_html_body(claim)}{ev_str}")

    non_validated_lines: list[str] = []
    for cd in ctx.get("non_validated_claims", []):
        raw = _sanitize_for_slack(cd.get("claim", ""))
        non_validated_lines.append(f"• {_to_telegram_html_body(raw)}")

    return validated_lines, non_validated_lines


def render_cloudwatch_link_html(ctx: ReportContext) -> str:
    """Telegram-HTML CloudWatch deep link, mirroring :func:`render_cloudwatch_link`."""
    cw_url = ctx.get("cloudwatch_logs_url")
    cw_group = ctx.get("cloudwatch_log_group")
    cw_stream = ctx.get("cloudwatch_log_stream")

    if cw_url:
        safe = html.escape(str(cw_url), quote=True)
        return f'\n<b>CloudWatch</b>: <a href="{safe}">View logs</a>\n'
    if cw_group and cw_stream:
        url = build_cloudwatch_url(ctx)
        if url:
            safe = html.escape(str(url), quote=True)
            return f'\n<b>CloudWatch</b>: <a href="{safe}">View logs</a>\n'
        return (
            f"\n<b>CloudWatch Logs</b>\n"
            f"Log Group: {html.escape(str(cw_group))}\n"
            f"Log Stream: {html.escape(str(cw_stream))}\n"
        )
    return ""


def _mrkdwn_section(text: str) -> "dict | None":
    """Build a Slack Block Kit section block with sanitized mrkdwn text.

    Slack section blocks have a 3000 char limit per text field.
    Returns None when text is empty — caller must skip None results.
    """
    sanitized = _sanitize_for_slack(text).strip()
    if not sanitized:
        return None
    if len(sanitized) > 2990:
        sanitized = sanitized[:2987] + "..."
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": sanitized},
    }


# ---------------------------------------------------------------------------
# Evidence tag resolution helpers
# ---------------------------------------------------------------------------

# Maps LLM source name → ordered list of evidence dict keys to try for a log message
_EVIDENCE_LOG_KEYS: dict[str, list[str]] = {
    "datadog_logs": ["datadog_error_logs", "datadog_logs"],
    "datadog": ["datadog_error_logs", "datadog_logs"],
    "grafana_logs": ["grafana_error_logs", "grafana_logs"],
    "grafana": ["grafana_error_logs", "grafana_logs"],
    "cloudwatch_logs": ["cloudwatch_logs"],
    "cloudwatch": ["cloudwatch_logs"],
}


def _extract_log_message(entry: object) -> str:
    """Extract a plain string message from a log entry that may be a dict or a string."""
    if isinstance(entry, dict):
        return (entry.get("message") or "").strip()
    return str(entry).strip()


def _resolve_evidence_tags(text: str, evidence: dict) -> str:
    """Replace [evidence: source] tags with the actual log message in a code span.

    Tries error logs first, then all logs for the named source. If no message
    is found the tag is removed silently to avoid leaking raw LLM annotations.
    """

    def _replace(m: re.Match) -> str:
        source = m.group(1).strip().lower()
        for key in _EVIDENCE_LOG_KEYS.get(source, []):
            logs = evidence.get(key) or []
            if logs:
                msg = _extract_log_message(logs[0])
                if msg:
                    return f": `{msg}`"
        return ""

    return re.sub(r"\s*\[(?i:evidence):\s*([^\]]+)\]", _replace, text).strip()


def _get_top_error_log(evidence: dict) -> str | None:
    """Return the first error log message from available evidence sources."""
    for key in (
        "datadog_error_logs",
        "datadog_logs",
        "grafana_error_logs",
        "grafana_logs",
        "cloudwatch_logs",
    ):
        logs = evidence.get(key) or []
        if logs:
            msg = _extract_log_message(logs[0])
            if msg:
                return msg
    return None


# ---------------------------------------------------------------------------
# Root cause derivation helpers
# ---------------------------------------------------------------------------


def _first_sentence(text: str) -> str:
    """Return the first sentence from text, normalized to one line."""
    cleaned = re.sub(r"(?:^|\s)#{1,6}\s+", " ", text, flags=re.MULTILINE)
    cleaned = re.sub(
        r"\b(?:Problem Statement|Summary|Context|Description|Overview)\b[:\s]*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    normalized = " ".join(cleaned.split()).strip()
    if not normalized:
        return ""

    parts = re.split(r"(?<=[.?!])\s+", normalized, maxsplit=1)
    sentence = parts[0]
    sentence = sentence.rstrip(".?!")
    return sentence


def _is_speculative(text: str) -> bool:
    speculative_terms = (" may ", " might ", " possibly", " possible ", " likely ")
    lower = f" {text.lower()} "
    return any(term in lower for term in speculative_terms)


def _remove_speculative_words(text: str) -> str:
    speculative = ("may", "might", "likely", "probably", "possibly")
    words = text.split()
    filtered = [w for w in words if w.lower() not in speculative]
    return " ".join(filtered)


def _derive_root_cause_sentence(ctx: ReportContext) -> str:
    """Derive a concise, single-sentence root cause with causal preference."""
    root_cause_text = ctx.get("root_cause", "") or ""
    root_cause_text = re.sub(r"\s*\[(?i:evidence):[^\]]*\]", "", root_cause_text).strip()
    validated_claims = ctx.get("validated_claims", [])

    if root_cause_text:
        sentence = _first_sentence(root_cause_text)
        if sentence and not _is_speculative(sentence):
            return sentence

    causal_connectors = (
        " because ",
        " due to ",
        " caused ",
        " resulted in ",
        " led to ",
        " root cause ",
        " failure triggered ",
    )

    for claim_data in validated_claims:
        claim = claim_data.get("claim", "") or ""
        claim = re.sub(r"\s*\[(?i:evidence):[^\]]*\]", "", claim).strip()
        lower = f" {claim.lower()} "
        if any(connector in lower for connector in causal_connectors):
            sentence = _first_sentence(claim)
            if sentence:
                return _first_sentence(_remove_speculative_words(sentence))

    if root_cause_text:
        sentence = _first_sentence(root_cause_text)
        if sentence:
            return sentence

    if validated_claims:
        claim = validated_claims[0].get("claim", "") or ""
        claim = re.sub(r"\s*\[(?i:evidence):[^\]]*\]", "", claim).strip()
        sentence = _first_sentence(claim)
        if sentence:
            return sentence

    return ""


# ---------------------------------------------------------------------------
# Public entry points — now live in the per-channel renderer modules.
# Re-exported here so existing imports (notably node.py) keep working.
# Imports go at the bottom so all helpers above are defined before the
# renderer modules import them back.
# ---------------------------------------------------------------------------

from app.delivery.publish_findings.formatters.renderers.slack import (  # noqa: E402
    build_slack_blocks,
    format_slack_message,
)
from app.delivery.publish_findings.formatters.renderers.telegram import (  # noqa: E402
    format_telegram_message,
)

__all__ = [
    "build_slack_blocks",
    "format_slack_message",
    "format_telegram_message",
    "render_cloudwatch_link",
    "render_cloudwatch_link_html",
]
