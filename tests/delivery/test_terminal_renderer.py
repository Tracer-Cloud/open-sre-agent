from __future__ import annotations

import pytest

from core.orchestration.node.publish_findings.renderers.terminal import (
    _rich_line_with_links,
    _strip_mrkdwn,
    _strip_slack_links,
    render_report,
)


def test_strip_mrkdwn_does_not_cross_lines_with_metric_regex() -> None:
    text = 'Run `{__name__=~"pipeline_runs_.*"}`\n\n*Cited Evidence:*'

    assert _strip_mrkdwn(text).endswith("\n\nCited Evidence:")


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Bold markers — the only formatting the function actually strips.
        ("*bold*", "bold"),
        ("text *bold* more", "text bold more"),
        ("*one*\n*two*", "one\ntwo"),
        # Italic, inline code, and fenced code are not handled by the renderer
        # and must pass through verbatim. These assertions document that
        # contract so a future change is forced to update the tests too.
        ("_italic_", "_italic_"),
        ("`code`", "`code`"),
        ("```block```", "```block```"),
        # Nested formatting: only bold is unwrapped; the inner italic markers
        # remain because the renderer does not touch them.
        ("*bold _italic_*", "bold _italic_"),
        # Empty and whitespace-only inputs are returned unchanged.
        ("", ""),
        ("   ", "   "),
        ("\n\n", "\n\n"),
        # Unicode and emoji content is preserved when wrappers are stripped.
        ("*café*", "café"),
        ("*🎉 party 🎉*", "🎉 party 🎉"),
        ("héllo wörld", "héllo wörld"),
        # No markdown -> pure passthrough.
        ("hello world", "hello world"),
        ("plain text with no markers at all.", "plain text with no markers at all."),
        # A lone `*` without a closing partner is left as-is.
        ("a * b", "a * b"),
        # The bold regex must not span newlines (regression test for the
        # existing happy-path case, kept parametrized for symmetry).
        ("*line1\nline2*", "*line1\nline2*"),
    ],
)
def test_strip_mrkdwn(raw: str, expected: str) -> None:
    assert _strip_mrkdwn(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Bare URL link -> the URL alone.
        ("<https://example.com>", "https://example.com"),
        # Labeled link -> "label (url)".
        ("<https://example.com|Example>", "Example (https://example.com)"),
        # Labeled link embedded in surrounding prose.
        (
            "Click <https://example.com|here> for details.",
            "Click here (https://example.com) for details.",
        ),
        # Multiple links in the same string are each rewritten.
        (
            "<https://a.test|A> and <https://b.test|B>",
            "A (https://a.test) and B (https://b.test)",
        ),
        # No Slack-style link -> passthrough (including bare URLs, which
        # _strip_slack_links is not responsible for).
        ("plain text", "plain text"),
        (
            "https://example.com without angle brackets",
            "https://example.com without angle brackets",
        ),
        # Empty input.
        ("", ""),
    ],
)
def test_strip_slack_links(raw: str, expected: str) -> None:
    assert _strip_slack_links(raw) == expected


@pytest.mark.parametrize(
    "raw, expected_plain",
    [
        # Plain text round-trips as-is.
        ("hello world", "hello world"),
        # Labeled Slack link is rendered using the label, not the URL.
        ("Click <https://example.com|here>!", "Click here!"),
        # Bare URL is preserved verbatim in the plain projection.
        ("see https://example.com for details", "see https://example.com for details"),
        # Bare Slack link (no label) falls back to the URL.
        ("<https://example.com>", "https://example.com"),
        # Empty string yields empty plain text.
        ("", ""),
    ],
)
def test_rich_line_with_links_plain_projection(raw: str, expected_plain: str) -> None:
    result = _rich_line_with_links(raw)

    assert result.plain == expected_plain


def test_rich_line_with_links_emits_link_span_for_labeled_url() -> None:
    result = _rich_line_with_links("Click <https://example.com|here>!")

    link_spans = [span for span in result.spans if "link https://example.com" in str(span.style)]
    assert link_spans, "expected a styled link span for the labeled URL"


# NOTE: documents current behavior, not desired behavior.
#
# `_URL_RE = r"https?://\S+"` greedily matches trailing punctuation because
# `.`, `,`, `;`, `)` are all non-whitespace. The renderer then `rstrip`s those
# characters off the URL used in the link span, but `sub_cursor` still advances
# to `m.end()` — so the punctuation is dropped from `result.plain` as well.
#
# A future fix that re-emits the stripped characters into the plain text should
# update the `expected_plain` column below to include the trailing punctuation.
@pytest.mark.parametrize(
    "raw, expected_url, expected_plain",
    [
        ("see https://example.com.", "https://example.com", "see https://example.com"),
        (
            "see https://example.com, then go.",
            "https://example.com",
            "see https://example.com then go.",
        ),
        ("see https://example.com; done", "https://example.com", "see https://example.com done"),
        ("see (https://example.com)", "https://example.com", "see (https://example.com"),
    ],
)
def test_rich_line_with_links_strips_trailing_punctuation_from_bare_url(
    raw: str, expected_url: str, expected_plain: str
) -> None:
    result = _rich_line_with_links(raw)

    link_spans = [span for span in result.spans if f"link {expected_url} " in str(span.style)]
    assert link_spans, f"expected a styled link span for the bare URL {expected_url!r}"
    assert result.plain == expected_plain


def test_rich_line_with_links_emits_link_span_for_bare_url() -> None:
    result = _rich_line_with_links("see https://example.com for details")

    link_spans = [span for span in result.spans if "link https://example.com" in str(span.style)]
    assert link_spans, "expected a styled link span for the bare URL"
    assert result.plain == "see https://example.com for details"


def test_render_report_plain_mode_strips_mrkdwn_and_slack_links(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "text")

    slack_message = (
        "## Findings\n"
        "- *high* cpu on <https://dashboards.example.com|grafana>\n"
        "*Cited Evidence:*\n"
        "- <https://logs.example.com|loki>\n"
    )

    render_report(slack_message)

    out = capsys.readouterr().out
    # Bold markers stripped; Slack links rewritten to "label (url)".
    assert "*high*" not in out
    assert "high cpu on grafana (https://dashboards.example.com)" in out
    assert "Cited Evidence:" in out
    assert "<https://logs.example.com|loki>" not in out
    assert "loki (https://logs.example.com)" in out


def test_render_report_empty_message_prints_no_report_generated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "text")

    render_report("")

    assert "No report generated." in capsys.readouterr().out
