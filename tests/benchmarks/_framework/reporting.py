from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

PAPER_BASELINES: dict[str, dict[str, float]] = {
    "deepseek-v3.2": {"a1": 0.73},
    "gpt-5": {"a1": 0.67},
    "gpt-4o": {"a1": 0.49},
    "claude-4-sonnet": {"a1": 0.50},
}


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results", [])
    by_llm: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if isinstance(result, dict):
            by_llm.setdefault(str(result.get("llm") or ""), []).append(result)

    comparisons: dict[str, dict[str, float]] = {}
    for llm, rows in by_llm.items():
        metrics = [
            (row.get("score") or {}).get("metrics", {})
            for row in rows
            if isinstance(row.get("score"), dict)
        ]
        a1_values = [float(metric["a1"]) for metric in metrics if "a1" in metric]
        if not a1_values:
            continue
        opensre_a1 = sum(a1_values) / len(a1_values)
        paper_a1 = PAPER_BASELINES.get(llm.lower(), {}).get("a1")
        comparisons[llm] = {
            "opensre_a1": opensre_a1,
            "paper_a1": paper_a1 if paper_a1 is not None else 0.0,
            "a1_lift": opensre_a1 - paper_a1 if paper_a1 is not None else 0.0,
        }
    return {"paper_baseline_comparison": comparisons}


def write_reports(payload: dict[str, Any], output_dir: Path, formats: tuple[str, ...]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    enriched = dict(payload)
    enriched["comparison"] = summarize_payload(payload)
    if "json" in formats:
        (output_dir / "summary.json").write_text(
            json.dumps(enriched, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if "markdown" in formats:
        (output_dir / "summary.md").write_text(render_markdown(enriched), encoding="utf-8")
    if "html" in formats:
        (output_dir / "summary.html").write_text(render_html(enriched), encoding="utf-8")


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# OpenSRE Benchmark Report",
        "",
        f"- Benchmark: `{payload.get('benchmark', '')}`",
        f"- Cases: {payload.get('case_count', 0)}",
        f"- Runs: {len(payload.get('results', []))}",
        f"- Estimated cost: ${float(payload.get('cost_usd', 0.0)):.4f}",
        "",
        "## LLM vs Paper Baseline",
        "",
        "| LLM | OpenSRE A@1 | Paper A@1 | Lift |",
        "| --- | ---: | ---: | ---: |",
    ]
    comparison = payload.get("comparison", {}).get("paper_baseline_comparison", {})
    if comparison:
        for llm, row in sorted(comparison.items()):
            lines.append(
                f"| {llm} | {row['opensre_a1']:.3f} | "
                f"{row['paper_a1']:.3f} | {row['a1_lift']:.3f} |"
            )
    else:
        lines.append("| - | - | - | - |")
    lines.extend(["", "## Notes", "", "- Expected deltas: validity > accuracy > speed."])
    return "\n".join(lines) + "\n"


def render_html(payload: dict[str, Any]) -> str:
    markdown = html.escape(render_markdown(payload))
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>OpenSRE Benchmark Report</title></head><body><pre>"
        f"{markdown}</pre></body></html>\n"
    )
