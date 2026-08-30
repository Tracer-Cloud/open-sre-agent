"""Tests for Slack mrkdwn escaping of untrusted evidence text.

Regression coverage for a real, exploitable gap: evidence-mapper-contributed
``summary``/``label``/``snippet``/``provenance`` text (issue titles, incident
names, service names, etc. -- all externally/caller controlled) was embedded
into Slack report text with no escaping. Slack's own ``&``/``<``/``>`` mrkdwn
syntax lets such text render as a live link or ``<!channel>``/``<!here>``
mention, and this project's own ``markdown_to_slack_mrkdwn`` additionally
converts any ``[text](url)`` pattern found later in the assembled report into
a real Slack link -- so untrusted text needs its brackets neutralized too,
not just the three characters Slack's spec calls out.
"""

from __future__ import annotations

from typing import Any

from tools.investigation.reporting.context import ReportContext
from tools.investigation.reporting.formatters.base import (
    escape_slack_mrkdwn,
    format_slack_link,
)
from tools.investigation.reporting.formatters.evidence import (
    format_cited_evidence_section,
)


class TestEscapeSlackMrkdwn:
    def test_escapes_ampersand_and_angle_brackets(self) -> None:
        assert escape_slack_mrkdwn("Tom & Jerry <script>") == "Tom &amp; Jerry &lt;script&gt;"

    def test_neutralizes_channel_mention_syntax(self) -> None:
        assert escape_slack_mrkdwn("<!channel> urgent") == "&lt;!channel&gt; urgent"

    def test_neutralizes_raw_slack_link_syntax(self) -> None:
        assert (
            escape_slack_mrkdwn("<https://evil.example/steal|click me>")
            == "&lt;https://evil.example/steal|click me&gt;"
        )

    def test_neutralizes_markdown_link_brackets(self) -> None:
        """Regression: markdown_to_slack_mrkdwn runs on the full report text
        after this escaping and would otherwise convert a literal
        [text](url) pattern in untrusted text into a live Slack link."""
        result = escape_slack_mrkdwn("[Click here](https://evil.example/steal)")
        assert "[" not in result
        assert "]" not in result
        assert "https://evil.example/steal" in result

    def test_plain_text_is_unchanged(self) -> None:
        assert escape_slack_mrkdwn("TypeError: cannot read property") == (
            "TypeError: cannot read property"
        )


class TestFormatSlackLinkEscaping:
    def test_escapes_label_without_url(self) -> None:
        assert format_slack_link("<!channel> alert", None) == "&lt;!channel&gt; alert"

    def test_escapes_label_and_url_when_present(self) -> None:
        result = format_slack_link("[fake](url)", "https://example.com/incidents/1?a=1&b=2")

        assert result == "<https://example.com/incidents/1?a=1&amp;b=2|［fake］(url)>"

    def test_neutralizes_pipe_in_url(self) -> None:
        """Regression: a literal "|" in the URL collides with Slack's own
        <url|label> delimiter and would corrupt the link structure."""
        result = format_slack_link("Incident", "https://example.com/x|y")

        assert result == "<https://example.com/x%7Cy|Incident>"


class TestFormatCitedEvidenceSectionEscaping:
    def _ctx_with_entry(self, **entry_overrides: object) -> ReportContext:
        entry: dict[str, Any] = {
            "display_id": "E1",
            "label": "Sentry Issue Details",
            "url": None,
            "summary": "'TypeError'",
            "snippet": None,
            "provenance": None,
        }
        entry.update(entry_overrides)
        return ReportContext(evidence_catalog={"evidence/mapped/get_sentry_issue_details": entry})

    def test_escapes_malicious_title_in_summary(self) -> None:
        ctx = self._ctx_with_entry(
            summary="'[Click here](https://evil.example/steal)', level error"
        )

        section = format_cited_evidence_section(ctx)

        assert "[Click here]" not in section
        assert "［Click here］" in section
        assert "https://evil.example/steal" in section

    def test_escapes_channel_mention_in_summary(self) -> None:
        ctx = self._ctx_with_entry(summary="<!channel> everyone look, error found")

        section = format_cited_evidence_section(ctx)

        assert "<!channel>" not in section
        assert "&lt;!channel&gt;" in section

    def test_escapes_provenance_and_snippet(self) -> None:
        ctx = self._ctx_with_entry(
            provenance="<!here> ping",
            snippet="[link](https://evil.example)",
        )

        section = format_cited_evidence_section(ctx)

        assert "<!here>" not in section
        assert "[link](" not in section

    def test_plain_summary_still_renders_normally(self) -> None:
        ctx = self._ctx_with_entry(summary="'TypeError: cannot read property', 3 event(s)")

        section = format_cited_evidence_section(ctx)

        assert "TypeError: cannot read property" in section
        assert "3 event(s)" in section
