from __future__ import annotations

import json

from tests.benchmarks._framework.reporting import summarize_payload, write_reports


def test_summarize_payload_compares_against_paper_baseline() -> None:
    summary = summarize_payload(
        {
            "results": [
                {"llm": "gpt-4o", "score": {"metrics": {"a1": 1.0}}},
                {"llm": "gpt-4o", "score": {"metrics": {"a1": 0.0}}},
            ]
        }
    )

    comparison = summary["paper_baseline_comparison"]["gpt-4o"]
    assert comparison["opensre_a1"] == 0.5
    assert comparison["paper_a1"] == 0.49
    assert round(comparison["a1_lift"], 2) == 0.01


def test_write_reports_writes_json_and_markdown(tmp_path) -> None:
    write_reports(
        {
            "benchmark": "cloudopsbench",
            "case_count": 1,
            "cost_usd": 0.0,
            "results": [{"llm": "gpt-5", "score": {"metrics": {"a1": 1.0}}}],
        },
        tmp_path,
        ("json", "markdown"),
    )

    assert json.loads((tmp_path / "summary.json").read_text())["benchmark"] == "cloudopsbench"
    assert "OpenSRE Benchmark Report" in (tmp_path / "summary.md").read_text()
