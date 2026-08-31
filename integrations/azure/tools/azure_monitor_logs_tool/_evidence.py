"""Evidence mapper for query_azure_monitor_logs."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from infrastructure.text.truncation import truncate

#: Bound the KQL query text echoed into a report summary -- it can be a long
#: or multi-line query built by the caller, not just the short bounded form.
_QUERY_SUMMARY_TRUNCATE_LEN = 80


def map_query_azure_monitor_logs(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the row count and a bounded snippet of the KQL query executed.

    ``effective_limit`` is the tool's own true ceiling -- it already folds in
    any caller-supplied row-cap clause (take/limit/sample/top) smaller than
    the tool's computed limit, so a returned count at that ceiling may
    understate how many rows actually matched -- use the "N+" convention.
    """
    if not output.get("available"):
        return
    total = output.get("total_returned", 0)
    if not total:
        return
    effective_limit = output.get("effective_limit", total)
    raw_query = str(output.get("query", ""))
    count_label = f"{total}+" if total >= effective_limit else str(total)
    query = truncate(
        raw_query.replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
        _QUERY_SUMMARY_TRUNCATE_LEN,
    )
    summary = f"{count_label} row(s)"
    if query:
        summary += f" for query '{query}'"
    record_evidence_entry(
        evidence,
        source="query_azure_monitor_logs",
        label="Azure Monitor Logs",
        summary=summary,
    )
