from __future__ import annotations

from app.delivery.publish_findings.renderers.terminal import _strip_mrkdwn


def test_strip_mrkdwn_does_not_cross_lines_with_metric_regex() -> None:
    text = 'Run `{__name__=~"pipeline_runs_.*"}`\n\n*Cited Evidence:*'

    assert _strip_mrkdwn(text).endswith("\n\nCited Evidence:")
