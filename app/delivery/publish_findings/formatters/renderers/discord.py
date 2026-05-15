"""Discord renderer — walks the shared section list, emits Discord markdown.

Two entry points:

- :func:`format_discord_message` — produces a Discord-markdown description
  body suitable for an embed's ``description`` field. Excludes the
  severity header; that information surfaces via the embed title +
  color bar in :func:`build_discord_embed`.
- :func:`build_discord_embed` — builds a complete embed payload
  (``title``, ``description``, ``color``, ``footer``) ready to hand to
  the Discord API. The severity drives both the title prefix emoji and
  the color of the embed's left-edge bar.

Discord markdown dialect notes:

- ``**bold**`` and ``*italic*`` work in both content and descriptions.
- Inline code ``` `like this` ``` and fenced code blocks work everywhere.
- ``[label](url)`` link syntax **only works in embed descriptions** —
  not in plain content messages. Since this renderer's output is always
  delivered inside an embed, the syntax is safe to emit.
- The list bullet ``- `` gets native list rendering on modern Discord
  clients; we use it instead of ``•`` to match the channel's idiom.
- ``<a href>``/``<b>`` (Telegram HTML) and ``<url|label>`` (Slack
  mrkdwn) do **not** render in Discord — they appear as literal text.

This renderer is intentionally self-contained: it walks
``Section.extras`` directly for EVIDENCE, FAILED_PODS, LINK, and META
rather than re-reading ``ctx``. The Slack/Telegram renderers still
delegate to legacy ctx-reading helpers for cited evidence and
cloudwatch — a follow-up will fold those into ``Section.extras`` and
collapse the duplication.
"""

from __future__ import annotations

import re
from typing import Any

from app.delivery.publish_findings.formatters.evidence import _format_tool_calls_line
from app.delivery.publish_findings.formatters.sections import (
    Section,
    SectionKind,
    prepare_sections_for,
)
from app.delivery.publish_findings.formatters.severity import severity_emoji
from app.delivery.publish_findings.report_context import ReportContext

# Slack <url|label> links can leak into section content through helpers like
# format_pod_line (used by build_investigation_trace) that pre-format their
# output for Slack. Translating them at render time keeps the Discord output
# clean. The structured-trace refactor that pushes link data into Section
# extras is tracked as a follow-up to issue #2007.
_SLACK_LINK_RE = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")

# Discord embed hard limits — https://discord.com/developers/docs/resources/channel#embed-limits
DISCORD_DESCRIPTION_LIMIT = 4096
DISCORD_TITLE_LIMIT = 256

# Severity → embed color (decimal RGB).
_SEVERITY_COLOR = {
    "critical": 0xE74C3C,
    "crit": 0xE74C3C,
    "high": 0xE67E22,
    "error": 0xE67E22,
    "medium": 0xF1C40F,
    "warning": 0xF1C40F,
    "warn": 0xF1C40F,
    "low": 0x2ECC71,
    "info": 0x2ECC71,
    "healthy": 0x2ECC71,
    "normal": 0x2ECC71,
    "none": 0x95A5A6,
}

# Fallback when severity is unknown — matches the legacy red the
# delivery layer used before this renderer existed.
_DEFAULT_COLOR = 0xE74C3C


def format_discord_link(label: str, url: str | None) -> str:
    """Return a Discord ``[label](<url>)`` link, or plain label without a URL.

    Brackets inside ``label`` are backslash-escaped so they don't terminate
    the link grammar early; an empty label after escaping falls back to
    the raw URL. The URL is wrapped in angle brackets (CommonMark
    "pointy-bracket" link destination) so any URL characters — closing
    parens, spaces, unbalanced punctuation — render correctly. Without
    the wrapping, a URL containing ``)`` would terminate the markdown
    link early and emit visible garbage to the user.
    """
    if not url:
        return label
    safe_label = label.replace("[", r"\[").replace("]", r"\]").strip() or url
    return f"[{safe_label}](<{url}>)"


def _translate_slack_links(text: str) -> str:
    """Replace Slack ``<url|label>`` link syntax with Discord ``[label](url)``."""
    return _SLACK_LINK_RE.sub(
        lambda m: format_discord_link(m.group(2) or m.group(1), m.group(1)), text
    )


def format_discord_message(ctx: ReportContext) -> str:
    """Render the RCA report as a Discord-markdown description body.

    Walks the channel-agnostic section list from
    :func:`prepare_sections_for` and dispatches by :class:`SectionKind`.
    The SEVERITY_HEADER section is intentionally not rendered here — it
    surfaces in :func:`build_discord_embed` as the embed title + color.
    """
    parts: list[str] = []
    for section in prepare_sections_for(ctx):
        chunk = _render(section, ctx)
        if chunk:
            parts.append(chunk)
    return "\n\n".join(parts)


def build_discord_embed(ctx: ReportContext) -> dict[str, Any]:
    """Build a complete Discord embed payload for the RCA report.

    Returns a dict ready to pass as one of the ``embeds`` entries on the
    Discord ``POST /channels/{id}/messages`` API. Truncates the
    description to fit Discord's 4096-char limit; severity drives both
    the title emoji and the embed's color bar.
    """
    sections = prepare_sections_for(ctx)
    severity = next((s for s in sections if s.kind is SectionKind.SEVERITY_HEADER), None)
    description = format_discord_message(ctx)
    if len(description) > DISCORD_DESCRIPTION_LIMIT:
        description = description[: DISCORD_DESCRIPTION_LIMIT - 1] + "…"
    return {
        "title": _embed_title(severity)[:DISCORD_TITLE_LIMIT],
        "color": _severity_color(severity),
        "description": description,
        "footer": {"text": "OpenSRE Investigation"},
    }


def _embed_title(severity: Section | None) -> str:
    if severity is None:
        return "Investigation Complete"
    alert = str(severity.extras.get("alert_name") or "Investigation Complete")
    sev = str(severity.extras.get("severity") or "")
    return f"{severity_emoji(sev)} {alert}"


def _severity_color(severity: Section | None) -> int:
    if severity is None:
        return _DEFAULT_COLOR
    sev = str(severity.extras.get("severity") or "").lower()
    return _SEVERITY_COLOR.get(sev, _DEFAULT_COLOR)


# ---------------------------------------------------------------------------
# Section walker
# ---------------------------------------------------------------------------


def _render(section: Section, ctx: ReportContext) -> str:
    kind = section.kind
    if kind is SectionKind.SEVERITY_HEADER:
        # Consumed by build_discord_embed for the title + color bar.
        return ""
    if kind is SectionKind.ROOT_CAUSE:
        return _render_root_cause(section)
    if kind is SectionKind.CLAIMS:
        return _render_claims(section)
    if kind is SectionKind.PROVENANCE:
        return _render_provenance(section)
    if kind is SectionKind.REMEDIATION:
        return _render_remediation(section)
    if kind is SectionKind.TRACE:
        return _render_trace(section)
    if kind is SectionKind.EVIDENCE:
        return _render_evidence(section, ctx)
    if kind is SectionKind.LINK:
        return _render_link(section)
    if kind is SectionKind.META:
        return _render_meta(section)
    # FAILED_PODS — parity gap with Slack Block Kit; the per-pod info still
    # surfaces in the investigation trace, so the channel is not blind to it.
    return ""


def _render_root_cause(section: Section) -> str:
    parts: list[str] = []
    if section.body:
        parts.append(section.body)
    top_log = section.extras.get("top_log")
    if top_log:
        parts.append(f"`{top_log}`")
    return "\n".join(parts)


def _render_claims(section: Section) -> str:
    title = section.title or "Findings"
    evidence_refs = section.extras.get("evidence_refs") or ()
    bullets: list[str] = []
    for idx, claim_text in enumerate(section.items):
        refs = evidence_refs[idx] if idx < len(evidence_refs) else ()
        evidence_part = ""
        if refs:
            labels = [
                format_discord_link(str(ref.get("display_id", "")), ref.get("url")) for ref in refs
            ]
            evidence_part = f" [{', '.join(labels)}]"
        bullets.append(f"- {_translate_slack_links(claim_text)}{evidence_part}")
    return f"**{title}**\n" + "\n".join(bullets)


def _render_provenance(section: Section) -> str:
    bullets = [f"- {item}" for item in section.items]
    return "**Provenance**\n" + "\n".join(bullets)


def _render_remediation(section: Section) -> str:
    bullets = [f"- {step}" for step in section.items]
    return "**Recommended Actions**\n" + "\n".join(bullets)


def _render_trace(section: Section) -> str:
    bullets = [_translate_slack_links(item) for item in section.items]
    return "**Investigation Trace**\n" + "\n".join(bullets)


def _render_evidence(section: Section, ctx: ReportContext) -> str:
    catalog = section.extras.get("catalog") or {}
    lines: list[str] = []

    def _sort_key(eid: str) -> str:
        return str((catalog.get(eid) or {}).get("display_id", eid))

    for evidence_id in sorted(catalog.keys(), key=_sort_key):
        entry = catalog[evidence_id] or {}
        display_id = entry.get("display_id", evidence_id)
        label = entry.get("label") or evidence_id
        url = entry.get("url")
        summary = entry.get("summary")
        snippet = entry.get("snippet")
        provenance = entry.get("provenance")
        link = format_discord_link(str(label), url)
        line = f"- {display_id} — {link}"
        if summary:
            line += f" — {summary}"
        if provenance:
            line += f" — provenance: {provenance}"
        if snippet:
            short = snippet if len(snippet) <= 100 else snippet[:97] + "..."
            line += f" — {short}"
        lines.append(line)

    # Tool-calls summary — parity with the Slack/Telegram cited-evidence
    # blocks. Reads ctx because the per-tool counts aren't promoted to a
    # section yet; folding them in is a follow-up.
    tcl = _format_tool_calls_line(ctx, link_fn=format_discord_link)
    if tcl:
        lines.append(f"- {tcl}")

    if not lines:
        return ""
    return "**Cited Evidence**\n" + "\n".join(lines)


def _render_link(section: Section) -> str:
    url = section.extras.get("url")
    label = str(section.extras.get("label") or "View logs")
    if url:
        return f"**CloudWatch**: {format_discord_link(label, str(url))}"
    group = section.extras.get("log_group")
    stream = section.extras.get("log_stream")
    if group and stream:
        return f"**CloudWatch Logs**\nLog Group: `{group}`\nLog Stream: `{stream}`"
    return ""


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
    return "*" + " | ".join(bits) + "*"
