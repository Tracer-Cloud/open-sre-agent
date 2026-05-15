"""Unit tests for the Discord renderer.

These specify the *new* behavior introduced by issue #2007 task #5 —
Discord no longer reuses Slack mrkdwn but emits Discord-native markdown.
Section ordering matches the Telegram parity target (severity header is
moved into the embed title + color bar rather than the description).
"""

from __future__ import annotations

import pytest

from app.delivery.publish_findings.formatters.renderers.discord import (
    DISCORD_DESCRIPTION_LIMIT,
    build_discord_embed,
    format_discord_link,
    format_discord_message,
)
from app.delivery.publish_findings.report_context import ReportContext, build_report_context
from tests.delivery.test_formatters_characterization import _rich_state


@pytest.fixture
def rich_ctx() -> ReportContext:
    ctx = build_report_context(_rich_state())
    ctx["investigation_duration_seconds"] = 42
    return ctx


def _assert_order(text: str, markers: list[str]) -> None:
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
# format_discord_link
# ---------------------------------------------------------------------------


def test_format_discord_link_with_url_returns_markdown_link() -> None:
    """URL is wrapped in angle brackets — CommonMark 'pointy-bracket' link destination."""
    assert (
        format_discord_link("OpenSRE", "https://example.com") == "[OpenSRE](<https://example.com>)"
    )


def test_format_discord_link_without_url_returns_plain_label() -> None:
    assert format_discord_link("OpenSRE", None) == "OpenSRE"
    assert format_discord_link("OpenSRE", "") == "OpenSRE"


def test_format_discord_link_escapes_brackets_in_label() -> None:
    out = format_discord_link("[oops]", "https://example.com")
    assert out == r"[\[oops\]](<https://example.com>)"


def test_format_discord_link_falls_back_to_url_when_label_empties() -> None:
    out = format_discord_link("   ", "https://example.com")
    assert out == "[https://example.com](<https://example.com>)"


# ---------------------------------------------------------------------------
# format_discord_message — section ordering and content
# ---------------------------------------------------------------------------


def test_format_discord_message_section_order(rich_ctx: ReportContext) -> None:
    body = format_discord_message(rich_ctx)
    _assert_order(
        body,
        [
            "exited with code 137",  # top_log under root cause
            "**Findings**",
            "**Non-Validated Claims (Inferred)**",
            "**Provenance**",
            "**Recommended Actions**",
            "**Investigation Trace**",
            "**Cited Evidence**",
            "**CloudWatch**",
        ],
    )


def test_format_discord_message_omits_severity_header(rich_ctx: ReportContext) -> None:
    """Severity surfaces via embed title + color, not in the description."""
    body = format_discord_message(rich_ctx)
    # No emoji+alert header line at the top.
    assert "🔴 **PodCrashLooping**" not in body
    # The alert name still appears in the root cause sentence, but the
    # standalone "<emoji> <bold alert> · <pipeline>" banner is absent.
    assert "**PodCrashLooping** · ingest" not in body


def test_format_discord_message_uses_discord_markdown_links(rich_ctx: ReportContext) -> None:
    body = format_discord_message(rich_ctx)
    # Evidence and cloudwatch links must use [label](<url>), not Slack <url|label>.
    assert "](<http" in body
    # No Slack-style angle-bracket pipe links.
    import re

    assert not re.search(r"<https?://[^|>]+\|", body), (
        f"Discord output leaked Slack-style link syntax:\n{body}"
    )


def test_format_discord_message_does_not_leak_html_or_slack_dialect(
    rich_ctx: ReportContext,
) -> None:
    body = format_discord_message(rich_ctx)
    # Telegram HTML must not appear.
    assert "<b>" not in body
    assert "<a href" not in body
    assert "<i>" not in body
    # Slack markdown headers must not appear (Discord doesn't render `## H2`
    # the same way; we deliberately use **bold** for section titles).
    assert "## Findings" not in body
    assert "## Recommended Actions" not in body


def test_format_discord_message_uses_dash_bullets(rich_ctx: ReportContext) -> None:
    """Discord renders `- ` as a native list; we prefer it over the `•` glyph."""
    body = format_discord_message(rich_ctx)
    assert "\n- Pod was OOMKilled per kubelet logs" in body
    assert "\n- Raise memory limit to 1Gi" in body


def test_format_discord_message_includes_top_log_as_inline_code(rich_ctx: ReportContext) -> None:
    body = format_discord_message(rich_ctx)
    assert "`container ingest-api exited with code 137 (OOMKilled)`" in body


def test_format_discord_message_includes_meta(rich_ctx: ReportContext) -> None:
    body = format_discord_message(rich_ctx)
    assert "Timing: 42s" in body
    assert "Alert ID: alert-abc-123" in body
    # Meta is rendered italic via single-star wrapper.
    assert "*Timing: 42s | Alert ID: alert-abc-123*" in body


# ---------------------------------------------------------------------------
# build_discord_embed
# ---------------------------------------------------------------------------


def test_build_discord_embed_title_uses_severity_emoji_and_alert_name(
    rich_ctx: ReportContext,
) -> None:
    embed = build_discord_embed(rich_ctx)
    assert embed["title"] == "🔴 PodCrashLooping"


def test_build_discord_embed_color_maps_to_severity(rich_ctx: ReportContext) -> None:
    embed = build_discord_embed(rich_ctx)
    assert embed["color"] == 0xE74C3C  # red for critical


def test_build_discord_embed_color_per_severity() -> None:
    cases = {
        "critical": 0xE74C3C,
        "high": 0xE67E22,
        "warning": 0xF1C40F,
        "info": 0x2ECC71,
        "none": 0x95A5A6,
    }
    for severity, expected_color in cases.items():
        state = _rich_state()
        state["severity"] = severity
        ctx = build_report_context(state)
        embed = build_discord_embed(ctx)
        assert embed["color"] == expected_color, (
            f"severity={severity!r} produced {embed['color']:#x}"
        )


def test_build_discord_embed_unknown_severity_falls_back_to_default_color() -> None:
    state = _rich_state()
    state["severity"] = "frobnicated"
    ctx = build_report_context(state)
    embed = build_discord_embed(ctx)
    assert embed["color"] == 0xE74C3C


def test_build_discord_embed_description_fits_under_4096(rich_ctx: ReportContext) -> None:
    embed = build_discord_embed(rich_ctx)
    assert len(embed["description"]) <= DISCORD_DESCRIPTION_LIMIT


def test_build_discord_embed_truncates_oversize_description() -> None:
    state = _rich_state()
    state["remediation_steps"] = ["x" * 200 for _ in range(50)]  # ~10000 chars
    ctx = build_report_context(state)
    embed = build_discord_embed(ctx)
    assert len(embed["description"]) <= DISCORD_DESCRIPTION_LIMIT
    assert embed["description"].endswith("…")


def test_build_discord_embed_has_footer(rich_ctx: ReportContext) -> None:
    embed = build_discord_embed(rich_ctx)
    assert embed["footer"] == {"text": "OpenSRE Investigation"}


def test_build_discord_embed_title_without_severity_section_uses_default() -> None:
    """When build_sections produces no SEVERITY_HEADER (missing alert/pipeline)."""
    state = _rich_state()
    state["alert_name"] = ""
    state["pipeline_name"] = ""
    ctx = build_report_context(state)
    embed = build_discord_embed(ctx)
    assert embed["title"] == "Investigation Complete"
    assert embed["color"] == 0xE74C3C  # default fallback


def test_build_discord_embed_title_respects_256_char_limit() -> None:
    state = _rich_state()
    state["alert_name"] = "X" * 500
    ctx = build_report_context(state)
    embed = build_discord_embed(ctx)
    assert len(embed["title"]) <= 256


# ---------------------------------------------------------------------------
# dedupe propagation — proves option (c) parity-by-construction
# ---------------------------------------------------------------------------


def test_discord_message_skips_banner_only_root_cause() -> None:
    """When build_sections + dedupe_sections drop the redundant ROOT_CAUSE,
    the Discord renderer reflects that automatically — no Discord-specific
    dedup logic required."""
    state = _rich_state()
    state["root_cause"] = "PodCrashLooping on ingest (severity: critical)"
    # Erase top_log so dedup drops the whole section.
    state["evidence"] = {}
    ctx = build_report_context(state)

    body = format_discord_message(ctx)
    # No root-cause sentence appears — header carries the same information
    # via the embed title.
    assert "PodCrashLooping on ingest (severity: critical)" not in body
