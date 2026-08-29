import pytest

from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence


@pytest.mark.parametrize(
    ("action", "result_key"),
    [
        ("search", "traces"),
        ("get_trace", "spans"),
        ("list_services", "services"),
        ("list_span_names", "span_names"),
    ],
)
def test_query_tempo_records_results_as_evidence(action: str, result_key: str) -> None:
    evidence: dict = {}

    merge_tool_evidence(
        evidence,
        "query_tempo",
        {"action": action, result_key: ["first", "second"]},
        {},
    )

    entry = evidence["catalog_entries"][0]
    assert entry["source"] == "query_tempo"
    assert entry["summary"] == f"2 {result_key.replace('_', ' ')}"

    empty: dict = {}
    merge_tool_evidence(empty, "query_tempo", {"action": action, result_key: []}, {})
    assert "catalog_entries" not in empty
