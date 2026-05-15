"""Per-channel edge-case tests for the section-based renderers.

Covers the kinds of subtle bugs the issue #2007 description called out:

- Stray markdown characters (``*``, ``_``, ``[``, ``]``) in claim text.
- Citation-heavy claims with multiple evidence references.
- LLM-emitted markdown that needs channel-specific translation (or
  preservation, in Discord's case).
- Special characters that could break each channel's link grammar.
- Length-limit boundaries (Slack block 3000-char cap, Discord title 256,
  description 4096).
- Cross-channel dedup propagation — proof that
  ``dedupe_sections`` reaches every renderer through the shared section
  list rather than per-channel logic.

Renderer-internal helpers (``_claim_bullets``) are exercised directly so
the tests stay isolated from ``ReportContext`` derivation; end-to-end
scenarios use ``build_report_context`` to ensure the pipeline composes.
"""

from __future__ import annotations

import re
from typing import Any

from app.delivery.publish_findings.formatters.renderers.discord import (
    build_discord_embed,
    format_discord_link,
    format_discord_message,
)
from app.delivery.publish_findings.formatters.renderers.slack import (
    _claim_bullets as _slack_claim_bullets,
)
from app.delivery.publish_findings.formatters.renderers.slack import (
    build_slack_blocks,
    format_slack_message,
)
from app.delivery.publish_findings.formatters.renderers.telegram import format_telegram_message
from app.delivery.publish_findings.formatters.sections import Section, SectionKind
from app.delivery.publish_findings.report_context import ReportContext, build_report_context

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(**overrides: Any) -> ReportContext:
    """Minimal ReportContext for edge-case tests with shallow override merge."""
    state: dict[str, Any] = {
        "pipeline_name": "pipe",
        "alert_name": "Alert",
        "severity": "warning",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
    }
    state.update(overrides)
    return build_report_context(state)


def _claims_section(
    items: list[str],
    *,
    evidence_refs: list[list[dict[str, Any]]] | None = None,
    validated: bool = True,
    title: str = "Findings",
) -> Section:
    """Build a CLAIMS section directly — bypasses build_sections so renderer
    logic can be tested with precisely controlled inputs."""
    refs = (
        tuple(tuple(rl) for rl in evidence_refs)
        if evidence_refs is not None
        else tuple(() for _ in items)
    )
    return Section(
        kind=SectionKind.CLAIMS,
        title=title,
        items=tuple(items),
        extras={"validated": validated, "evidence_refs": refs},
    )


# ---------------------------------------------------------------------------
# Slack mrkdwn — claim formatting edge cases
# ---------------------------------------------------------------------------


def test_slack_claim_with_double_asterisks_converts_to_single() -> None:
    """LLM-emitted ``**bold**`` becomes Slack ``*bold*`` via _sanitize_for_slack."""
    section = _claims_section(["Check **memory limit**"])
    bullets = _slack_claim_bullets(section)
    assert "*memory limit*" in bullets[0]
    assert "**memory limit**" not in bullets[0]


def test_slack_claim_with_markdown_header_converts_to_bold() -> None:
    """LLM-emitted ``## Header`` becomes Slack ``*Header*``."""
    section = _claims_section(["## Memory Pressure"])
    bullets = _slack_claim_bullets(section)
    assert "*Memory Pressure*" in bullets[0]
    assert "## Memory Pressure" not in bullets[0]


def test_slack_claim_with_three_evidence_refs_renders_all_inline() -> None:
    """Multiple refs render as ``[<url1|E1>, <url2|E2>, E3]`` — comma-separated."""
    section = _claims_section(
        ["Multi-source agreement"],
        evidence_refs=[
            [
                {"display_id": "E1", "url": "https://example.com/1"},
                {"display_id": "E2", "url": "https://example.com/2"},
                {"display_id": "E3", "url": None},
            ]
        ],
    )
    bullets = _slack_claim_bullets(section)
    assert "[<https://example.com/1|E1>, <https://example.com/2|E2>, E3]" in bullets[0]


def test_slack_blocks_truncate_oversize_section_to_3000_char_cap() -> None:
    """Slack section blocks limit ``mrkdwn.text`` to 3000 chars; ``_mrkdwn_section``
    truncates to keep the API from rejecting the payload."""
    long_steps = ["x" * 200] * 50  # ~10k chars across remediation
    ctx = _ctx(remediation_steps=long_steps)
    blocks = build_slack_blocks(ctx)
    section_lengths = [
        len(b["text"]["text"]) for b in blocks if b.get("type") == "section" and "text" in b
    ]
    assert section_lengths, "expected at least one section block"
    assert max(section_lengths) <= 3000


# ---------------------------------------------------------------------------
# Discord — markdown preservation and link escaping
# ---------------------------------------------------------------------------


def test_discord_claim_preserves_double_asterisks() -> None:
    """Discord renders ``**bold**`` natively — no translation."""
    ctx = _ctx(validated_claims=[{"claim": "Check **memory limit**"}])
    body = format_discord_message(ctx)
    assert "**memory limit**" in body


def test_discord_claim_preserves_markdown_headers() -> None:
    """Discord supports ``## Header`` natively; renderer doesn't rewrite it."""
    ctx = _ctx(validated_claims=[{"claim": "## Memory Pressure"}])
    body = format_discord_message(ctx)
    assert "## Memory Pressure" in body


def test_discord_link_handles_paren_in_url_via_angle_wrap() -> None:
    """``[label](url)`` would break on ``)`` in the URL; ``[label](<url>)`` survives."""
    out = format_discord_link("docs", "https://example.com/path(name)")
    assert out == "[docs](<https://example.com/path(name)>)"


def test_discord_embed_title_truncates_oversize_alert_name() -> None:
    ctx = _ctx(alert_name="X" * 500)
    embed = build_discord_embed(ctx)
    assert len(embed["title"]) <= 256


def test_discord_claim_with_three_evidence_refs_renders_all_inline() -> None:
    section = _claims_section(
        ["Multi-source agreement"],
        evidence_refs=[
            [
                {"display_id": "E1", "url": "https://example.com/1"},
                {"display_id": "E2", "url": "https://example.com/2"},
                {"display_id": "E3", "url": None},
            ]
        ],
    )
    from app.delivery.publish_findings.formatters.renderers.discord import _render_claims

    rendered = _render_claims(section)
    assert "[E1](<https://example.com/1>)" in rendered
    assert "[E2](<https://example.com/2>)" in rendered
    assert "E3" in rendered  # no URL → plain label
    # The three refs are bundled in one bracketed cluster.
    assert "[E1](<https://example.com/1>), [E2](<https://example.com/2>), E3" in rendered


# ---------------------------------------------------------------------------
# Telegram HTML — escaping and citation rendering
# ---------------------------------------------------------------------------


def test_telegram_claim_escapes_html_special_chars() -> None:
    """Literal ``<``, ``>``, ``&`` in claim text must be HTML-escaped."""
    ctx = _ctx(
        validated_claims=[{"claim": "Container <web-1> failed with code & error"}],
    )
    body = format_telegram_message(ctx)
    assert "&lt;web-1&gt;" in body
    assert "<web-1>" not in body
    assert "with code &amp; error" in body


def test_telegram_claim_converts_double_asterisks_to_bold_tag() -> None:
    ctx = _ctx(validated_claims=[{"claim": "Check **memory limit**"}])
    body = format_telegram_message(ctx)
    assert "<b>memory limit</b>" in body


def test_telegram_claim_translates_slack_link_to_html_anchor() -> None:
    """Claim text containing Slack ``<url|label>`` becomes ``<a href>label</a>``."""
    ctx = _ctx(
        validated_claims=[{"claim": "See <https://example.com|the docs> for details"}],
    )
    body = format_telegram_message(ctx)
    assert '<a href="https://example.com">the docs</a>' in body


def test_telegram_claim_with_three_evidence_refs_renders_anchors() -> None:
    """Multiple refs render as ``[<a href>E1</a>, <a href>E2</a>, E3]``."""
    section = _claims_section(
        ["Multi-source agreement"],
        evidence_refs=[
            [
                {"display_id": "E1", "url": "https://example.com/1"},
                {"display_id": "E2", "url": "https://example.com/2"},
                {"display_id": "E3", "url": None},
            ]
        ],
    )
    from app.delivery.publish_findings.formatters.renderers.telegram import _render_claims

    rendered = _render_claims(section)
    assert '<a href="https://example.com/1">E1</a>' in rendered
    assert '<a href="https://example.com/2">E2</a>' in rendered
    # No-URL ref renders as escaped plain text.
    assert "E3" in rendered


# ---------------------------------------------------------------------------
# Cross-channel parity — option (c) proof
# ---------------------------------------------------------------------------


def _banner_only_state() -> dict[str, Any]:
    return {
        "pipeline_name": "ingest",
        "alert_name": "PodCrash",
        "severity": "critical",
        "root_cause": "PodCrash on ingest (severity: critical)",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
    }


def test_banner_only_root_cause_is_dropped_in_all_channels() -> None:
    """The dedup heuristic in ``dedupe_sections`` runs once; all three
    renderers reflect the drop without channel-specific dedup logic."""
    ctx = build_report_context(_banner_only_state())
    redundant = "PodCrash on ingest (severity: critical)"

    assert redundant not in format_slack_message(ctx)
    assert redundant not in format_telegram_message(ctx)
    assert redundant not in format_discord_message(ctx)


def test_empty_severity_falls_back_to_unknown_tier_across_channels() -> None:
    """Empty severity → ⚠️ emoji and 'UNKNOWN' tier in every channel.

    The fallback ``severity_display('') == 'UNKNOWN'`` lets users distinguish
    "no severity set" from a legitimate severity tier — important for noisy
    alerts that arrive without a severity label.
    """
    ctx = _ctx(severity="")
    slack_text = format_slack_message(ctx)
    telegram_text = format_telegram_message(ctx)
    embed = build_discord_embed(ctx)

    assert "⚠️" in slack_text and "UNKNOWN" in slack_text
    assert "⚠️" in telegram_text and "UNKNOWN" in telegram_text
    assert "⚠️" in embed["title"]


def test_unmapped_severity_preserves_raw_label_across_channels() -> None:
    """A non-empty but unrecognised severity (e.g. typo, new tier) keeps the
    raw label (uppercased) but still gets the ⚠️ fallback emoji."""
    ctx = _ctx(severity="frobnicated")
    slack_text = format_slack_message(ctx)
    telegram_text = format_telegram_message(ctx)
    embed = build_discord_embed(ctx)

    assert "⚠️" in slack_text and "FROBNICATED" in slack_text
    assert "⚠️" in telegram_text and "FROBNICATED" in telegram_text
    assert "⚠️" in embed["title"]


def test_empty_investigation_renders_not_determined_fallback_in_all_channels() -> None:
    """Regression for greptile review feedback on PR #2057: when an
    investigation produces neither a derived root-cause sentence nor any
    error logs, every channel must still render the legacy
    "Not determined (insufficient evidence)." fallback so the user can
    distinguish "we finished and found nothing" from a silent rendering
    failure."""
    state = {
        "pipeline_name": "watchdog",
        "alert_name": "Heartbeat",
        "severity": "info",
        "root_cause": "",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
    }
    ctx = build_report_context(state)

    fallback = "Not determined (insufficient evidence)."
    assert fallback in format_slack_message(ctx)
    assert fallback in format_telegram_message(ctx)
    assert fallback in format_discord_message(ctx)


def test_no_channel_leaks_foreign_dialect_syntax() -> None:
    """A single ctx run through every renderer: each output is free of the
    other channels' dialect tokens."""
    ctx = build_report_context(
        {
            "pipeline_name": "ingest",
            "alert_name": "PodCrash",
            "severity": "critical",
            "root_cause": "PodCrash on ingest because of OOM",
            "validated_claims": [{"claim": "OOM observed", "evidence_sources": []}],
            "non_validated_claims": [],
            "evidence": {},
        }
    )

    slack_text = format_slack_message(ctx)
    telegram_text = format_telegram_message(ctx)
    discord_text = format_discord_message(ctx)

    # Slack mrkdwn: no Telegram HTML, no Discord [label](<url>).
    assert "<b>" not in slack_text
    assert "<a href" not in slack_text
    assert not re.search(r"\]\(<https?", slack_text)

    # Telegram HTML: no Slack <url|label>, no Discord [label](<url>).
    assert not re.search(r"<https?://[^|>]+\|", telegram_text)
    assert not re.search(r"\]\(<https?", telegram_text)
    assert "## " not in telegram_text  # ## headings would not render

    # Discord markdown: no Slack <url|label>, no Telegram <b>/<a href>.
    assert not re.search(r"<https?://[^|>]+\|", discord_text)
    assert "<b>" not in discord_text
    assert "<a href" not in discord_text
