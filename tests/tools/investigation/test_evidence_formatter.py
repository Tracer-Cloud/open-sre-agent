from __future__ import annotations

from tools.investigation.reporting.formatters.evidence import _format_tool_calls_line


def test_format_tool_calls_line_preserves_first_seen_order_and_dedupes_actions() -> None:
    ctx = {
        "executed_hypotheses": [
            {
                "actions": [
                    "query_datadog_logs",
                    "get_cloudwatch_logs",
                    "query_datadog_logs",
                ]
            },
            {
                "actions": [
                    "get_cloudwatch_logs",
                    "query_datadog_logs",
                    "query_betterstack_logs",
                ]
            },
        ],
        "evidence": {
            "cloudwatch_logs": [{"message": "one"}, {"message": "two"}],
            "datadog_logs": [{"message": "one"}, {"message": "two"}],
            "betterstack_logs": [{"message": "one"}],
        },
    }

    line = _format_tool_calls_line(ctx, link_fn=lambda label, _url: label)

    assert line == (
        "Queries: Datadog Logs (2 logs), cloudwatch logs (2 events), Better Stack Logs (1 rows)"
    )
