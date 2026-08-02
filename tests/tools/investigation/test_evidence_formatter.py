"""Tests for investigation evidence report formatting."""

from tools.investigation.reporting.context import ReportContext
from tools.investigation.reporting.formatters.evidence import _format_tool_calls_line


def test_tool_calls_line_deduplicates_actions_in_first_seen_order() -> None:
    ctx: ReportContext = {
        "executed_hypotheses": [
            {"actions": ["query_logs", "inspect_metrics"]},
            {"actions": ["query_logs", "check_deploy"]},
        ]
    }

    assert _format_tool_calls_line(ctx) == ("Queries: query logs, inspect metrics, check deploy")
