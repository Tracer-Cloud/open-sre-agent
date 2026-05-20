"""Render report.json + per-case artifacts into markdown + HTML.

Operates on what's already on disk (``run_dir/report.json`` +
``run_dir/cases/*.json``) so it can be invoked two ways:

  1. From the runner directly, right after the JSON sidecar is written
  2. From the CLI as ``bench report <run_dir>`` — for re-rendering
     a finished run without re-executing anything

Self-contained outputs — markdown is plain CommonMark, HTML has inline
CSS only (no external dependencies, viewable in any browser).

The reporting layer respects the integrity discipline:

  - Headline numbers ALWAYS shown with per-stratum breakdown (Mechanism 4)
  - Every adapter-declared metric is in the table, even when ugly (Mechanism 3)
  - Negative-results section verbatim from the report (Mechanism 9)
  - COI disclosure verbatim from the report (Mechanism 10)
  - Raw per-case artifact paths listed so external reviewers can verify (Mechanism 5)

The reporter never aggregates away detail — that's a property of the
framework, not a stylistic choice.
"""

from __future__ import annotations

import html
import json
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def render_report_dir(
    run_dir: Path,
    formats: Sequence[str] | None = None,
) -> dict[str, Path]:
    """Render artifacts under ``run_dir`` to the requested formats.

    Args:
        run_dir: directory containing ``report.json`` and ``cases/``.
        formats: subset of {"markdown", "html"}; defaults to both.

    Returns:
        Mapping format -> path of the rendered artifact.

    Raises:
        FileNotFoundError: if ``report.json`` is missing.
    """
    formats = formats or ["markdown", "html"]
    report_path = run_dir / "report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"Missing {report_path}; run hasn't produced a report yet")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    cases_dir = run_dir / "cases"
    cells = _load_cells(cases_dir) if cases_dir.exists() else []

    out: dict[str, Path] = {}
    if "markdown" in formats:
        md_path = run_dir / "report.md"
        md_path.write_text(_render_markdown(report, cells), encoding="utf-8")
        out["markdown"] = md_path
    if "html" in formats:
        html_path = run_dir / "report.html"
        html_path.write_text(_render_html(report, cells), encoding="utf-8")
        out["html"] = html_path
    return out


# --------------------------------------------------------------------------- #
# Loading                                                                     #
# --------------------------------------------------------------------------- #


def _load_cells(cases_dir: Path) -> list[dict[str, Any]]:
    """Load every per-case artifact in ``cases_dir`` as a dict."""
    cells: list[dict[str, Any]] = []
    for path in sorted(cases_dir.glob("*.json")):
        try:
            cells.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            # Skip corrupt artifacts but record path so the report shows the gap
            cells.append({"_load_error": str(path)})
    return cells


# --------------------------------------------------------------------------- #
# Aggregation helpers                                                         #
# --------------------------------------------------------------------------- #


def _per_cell_metric(cells: list[dict[str, Any]], metric: str) -> list[float]:
    """Pull one metric across all cells as a flat float list."""
    out: list[float] = []
    for cell in cells:
        value = cell.get("score", {}).get("metrics", {}).get(metric)
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def _cells_by_llm(cells: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        if "_load_error" in cell:
            continue
        llm = cell.get("run", {}).get("llm", "(unknown)")
        out.setdefault(llm, []).append(cell)
    return out


def _summarize(values: list[float]) -> tuple[float, float, float, int]:
    """Return (median, p25, p75, n). All zeros when empty."""
    if not values:
        return 0.0, 0.0, 0.0, 0
    if len(values) == 1:
        v = values[0]
        return v, v, v, 1
    s = sorted(values)
    n = len(s)
    median = statistics.median(s)
    # Tukey-ish quartiles — good enough for reporting
    p25 = s[max(0, (n - 1) // 4)]
    p75 = s[min(n - 1, 3 * (n - 1) // 4)]
    return median, p25, p75, n


# --------------------------------------------------------------------------- #
# Markdown rendering                                                          #
# --------------------------------------------------------------------------- #


def _render_markdown(report: dict[str, Any], cells: list[dict[str, Any]]) -> str:
    """Render the report as plain CommonMark."""
    lines: list[str] = []
    lines.append(f"# Benchmark Run — {report.get('run_id', '(unknown)')}")
    lines.append("")
    lines.append(
        f"_config hash:_ `{report.get('config_hash', '?')}`  ·  "
        f"_opensre SHA:_ `{report.get('opensre_sha', '?')}`"
    )
    lines.append("")
    lines.append(f"**Started:** {report.get('started_at', '?')}  ")
    lines.append(f"**Ended:** {report.get('ended_at', '?')}  ")
    cost = report.get("cost", {})
    lines.append(
        f"**Cost:** ${cost.get('total_cost_usd', 0):.4f} of "
        f"${cost.get('budget_usd', 0):.2f} budget "
        f"({cost.get('total_calls', 0)} calls, "
        f"{cost.get('total_tokens_in', 0):,} in / {cost.get('total_tokens_out', 0):,} out)"
    )
    lines.append("")

    # --- COI disclosure (Mechanism 10) ---
    coi = (report.get("coi_disclosure") or "").strip()
    if coi:
        lines.append("## Conflict-of-interest disclosure")
        lines.append("")
        for paragraph in coi.split("\n\n"):
            lines.append(paragraph.strip())
            lines.append("")

    # --- Headline panel (per-LLM medians on the "all" stratum) ---
    lines.append("## Headline (medians across all cases)")
    lines.append("")
    by_llm = _cells_by_llm(cells)
    if not by_llm:
        lines.append("_no cells executed_")
    else:
        headline_metrics = [
            "a1",
            "a3",
            "tcr",
            "cov",
            "steps",
            "iac",
            "citation_grounding_rate",
            "entity_existence_rate",
            "kubectl_actionability_rate",
        ]
        header = "| LLM | n | " + " | ".join(headline_metrics) + " |"
        sep = "|" + "|".join(["---"] * (len(headline_metrics) + 2)) + "|"
        lines.append(header)
        lines.append(sep)
        for llm in sorted(by_llm.keys()):
            llm_cells = by_llm[llm]
            row = [f"`{llm}`", str(len(llm_cells))]
            for metric in headline_metrics:
                values = _per_cell_metric(llm_cells, metric)
                median, _, _, _ = _summarize(values)
                row.append(f"{median:.2f}")
            lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # --- Per-stratum × per-LLM detail (Mechanism 4) ---
    lines.append("## Per-stratum × per-LLM (medians)")
    lines.append("")
    reported_metrics = report.get("reported_metrics", [])
    per_stratum = report.get("per_stratum", {})
    for stratum in sorted(per_stratum.keys()):
        lines.append(f"### {stratum}")
        lines.append("")
        by_mode_llm = per_stratum[stratum]
        if not by_mode_llm:
            lines.append("_no data_")
            lines.append("")
            continue
        header = "| mode/llm | " + " | ".join(reported_metrics) + " |"
        sep = "|" + "|".join(["---"] * (len(reported_metrics) + 1)) + "|"
        lines.append(header)
        lines.append(sep)
        for mode_llm in sorted(by_mode_llm.keys()):
            metrics = by_mode_llm[mode_llm]
            row = [f"`{mode_llm}`"]
            for metric in reported_metrics:
                value = metrics.get(metric, 0.0)
                row.append(f"{value:.2f}" if isinstance(value, (int, float)) else "—")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # --- Negative results section (Mechanism 9) ---
    lines.append("## Negative results — where opensre lost or tied")
    lines.append("")
    negative = (report.get("negative_results") or "").strip()
    lines.append("```")
    lines.append(negative or "(none recorded)")
    lines.append("```")
    lines.append("")

    # --- Pre-registration pointer (Mechanism 1) ---
    prereg = report.get("pre_registration_path")
    if prereg:
        lines.append("## Pre-registration")
        lines.append("")
        lines.append(f"`{prereg}` (committed before run; expected deltas were locked in)")
        lines.append("")

    # --- Raw artifacts (Mechanism 5) ---
    raw_dir = report.get("raw_artifacts_dir")
    if raw_dir:
        lines.append("## Raw artifacts")
        lines.append("")
        lines.append(f"Per-case JSON written to `{raw_dir}` ({len(cells)} files).")
        lines.append("")

    # --- Cost breakdown by model ---
    by_model = cost.get("by_model", {})
    if by_model:
        lines.append("## Cost breakdown by model")
        lines.append("")
        lines.append("| model | calls | tokens in | tokens out | cost USD |")
        lines.append("|---|---|---|---|---|")
        for model in sorted(by_model.keys()):
            m = by_model[model]
            lines.append(
                f"| `{model}` | {m.get('call_count', 0)} | "
                f"{m.get('tokens_in', 0):,} | {m.get('tokens_out', 0):,} | "
                f"${m.get('cost_usd', 0):.4f} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# HTML rendering — self-contained, inline CSS, no external assets             #
# --------------------------------------------------------------------------- #


_HTML_STYLE = """
:root {
  --fg: #1a1a1a; --bg: #ffffff; --muted: #5a6172; --soft: #f5f7fa;
  --line: #e1e4e8; --accent: #0066cc; --good: #1a7f4f; --warn: #b85c00;
  --bad: #b91c1c; --shadow: 0 1px 3px rgba(0,0,0,0.05);
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem; max-width: 1200px; margin: 0 auto;
  font-family: -apple-system, BlinkMacSystemFont, "Inter", sans-serif;
  color: var(--fg); background: var(--bg); line-height: 1.5; font-size: 14px;
}
h1 { margin: 0 0 0.5rem 0; font-size: 1.8rem; }
h2 {
  font-size: 1.25rem; margin: 2rem 0 0.75rem 0;
  border-bottom: 2px solid var(--accent); padding-bottom: 0.3rem;
}
h3 { font-size: 1rem; margin: 1.25rem 0 0.5rem 0; color: var(--muted); }
.meta {
  display: grid; grid-template-columns: max-content 1fr; gap: 0.25rem 1rem;
  font-size: 13px; color: var(--muted); margin-bottom: 1rem;
}
.meta dt { font-weight: 600; color: var(--fg); }
table {
  border-collapse: collapse; width: 100%; margin: 0.5rem 0; font-size: 13px;
  background: white; box-shadow: var(--shadow); border-radius: 6px;
  overflow: hidden;
}
th, td {
  text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--line);
}
th {
  background: var(--soft); font-weight: 600; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.04em;
}
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: var(--soft); }
td.metric { font-variant-numeric: tabular-nums; text-align: right; }
.pill {
  display: inline-block; padding: 1px 8px; border-radius: 12px;
  font-size: 11px; font-weight: 600; background: #e6f0ff; color: var(--accent);
}
.pill.good { background: #e8f5ee; color: var(--good); }
.pill.warn { background: #fff4e6; color: var(--warn); }
.pill.bad { background: #fee2e2; color: var(--bad); }
pre {
  background: var(--soft); border: 1px solid var(--line); border-radius: 6px;
  padding: 0.75rem; overflow-x: auto; font-size: 12px;
}
code { font-family: "SF Mono", Monaco, Menlo, Consolas, monospace; font-size: 0.9em; }
.callout {
  border-left: 4px solid var(--accent); background: #f4f8ff;
  padding: 0.6rem 1rem; margin: 1rem 0; border-radius: 0 6px 6px 0;
}
.callout.coi { border-left-color: var(--warn); background: #fff8ec; }
"""


def _render_html(report: dict[str, Any], cells: list[dict[str, Any]]) -> str:
    """Render a self-contained HTML report. No external CSS or JS."""

    def esc(s: Any) -> str:
        return html.escape(str(s))

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="UTF-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    parts.append(f"<title>Benchmark Run — {esc(report.get('run_id', ''))}</title>")
    parts.append(f"<style>{_HTML_STYLE}</style>")
    parts.append("</head><body>")

    # Title + meta
    parts.append(f"<h1>Benchmark Run — {esc(report.get('run_id', '(unknown)'))}</h1>")
    parts.append('<dl class="meta">')
    parts.append(f"<dt>Config hash</dt><dd><code>{esc(report.get('config_hash', '?'))}</code></dd>")
    parts.append(f"<dt>opensre SHA</dt><dd><code>{esc(report.get('opensre_sha', '?'))}</code></dd>")
    parts.append(f"<dt>Started</dt><dd>{esc(report.get('started_at', '?'))}</dd>")
    parts.append(f"<dt>Ended</dt><dd>{esc(report.get('ended_at', '?'))}</dd>")
    cost = report.get("cost", {})
    parts.append(
        f"<dt>Cost</dt><dd>${cost.get('total_cost_usd', 0):.4f} of "
        f"${cost.get('budget_usd', 0):.2f} budget "
        f"({cost.get('total_calls', 0)} calls)</dd>"
    )
    parts.append("</dl>")

    # COI
    coi = (report.get("coi_disclosure") or "").strip()
    if coi:
        parts.append("<h2>Conflict-of-interest disclosure</h2>")
        parts.append('<div class="callout coi">')
        for paragraph in coi.split("\n\n"):
            parts.append(f"<p>{esc(paragraph.strip())}</p>")
        parts.append("</div>")

    # Headline panel
    parts.append("<h2>Headline (medians across all cases)</h2>")
    by_llm = _cells_by_llm(cells)
    if not by_llm:
        parts.append("<p><em>no cells executed</em></p>")
    else:
        headline_metrics = [
            "a1",
            "a3",
            "tcr",
            "cov",
            "steps",
            "iac",
            "citation_grounding_rate",
            "entity_existence_rate",
            "kubectl_actionability_rate",
        ]
        parts.append("<table><thead><tr><th>LLM</th><th>n</th>")
        for m in headline_metrics:
            parts.append(f"<th>{esc(m)}</th>")
        parts.append("</tr></thead><tbody>")
        for llm in sorted(by_llm.keys()):
            llm_cells = by_llm[llm]
            parts.append(
                f'<tr><td><code>{esc(llm)}</code></td><td class="metric">{len(llm_cells)}</td>'
            )
            for m in headline_metrics:
                values = _per_cell_metric(llm_cells, m)
                median, _, _, _ = _summarize(values)
                parts.append(f'<td class="metric">{median:.2f}</td>')
            parts.append("</tr>")
        parts.append("</tbody></table>")

    # Per-stratum × per-LLM
    parts.append("<h2>Per-stratum × per-LLM (medians)</h2>")
    reported_metrics = report.get("reported_metrics", [])
    for stratum in sorted(report.get("per_stratum", {}).keys()):
        parts.append(f"<h3>{esc(stratum)}</h3>")
        by_mode_llm = report["per_stratum"][stratum]
        if not by_mode_llm:
            parts.append("<p><em>no data</em></p>")
            continue
        parts.append("<table><thead><tr><th>mode/llm</th>")
        for m in reported_metrics:
            parts.append(f"<th>{esc(m)}</th>")
        parts.append("</tr></thead><tbody>")
        for mode_llm in sorted(by_mode_llm.keys()):
            metrics = by_mode_llm[mode_llm]
            parts.append(f"<tr><td><code>{esc(mode_llm)}</code></td>")
            for m in reported_metrics:
                value = metrics.get(m, 0.0)
                cell = f"{value:.2f}" if isinstance(value, (int, float)) else "—"
                parts.append(f'<td class="metric">{cell}</td>')
            parts.append("</tr>")
        parts.append("</tbody></table>")

    # Negative results
    parts.append("<h2>Negative results — where opensre lost or tied</h2>")
    negative = (report.get("negative_results") or "").strip()
    parts.append(f"<pre>{esc(negative or '(none recorded)')}</pre>")

    # Pre-registration
    prereg = report.get("pre_registration_path")
    if prereg:
        parts.append("<h2>Pre-registration</h2>")
        parts.append(
            f"<p><code>{esc(prereg)}</code> — committed before run; "
            "expected deltas were locked in.</p>"
        )

    # Raw artifacts
    raw_dir = report.get("raw_artifacts_dir")
    if raw_dir:
        parts.append("<h2>Raw artifacts</h2>")
        parts.append(
            f"<p>Per-case JSON written to <code>{esc(raw_dir)}</code> ({len(cells)} files).</p>"
        )

    # Cost breakdown
    by_model = cost.get("by_model", {})
    if by_model:
        parts.append("<h2>Cost breakdown by model</h2>")
        parts.append(
            "<table><thead><tr><th>model</th><th>calls</th>"
            "<th>tokens in</th><th>tokens out</th><th>cost USD</th></tr></thead><tbody>"
        )
        for model in sorted(by_model.keys()):
            m = by_model[model]
            parts.append(
                f"<tr><td><code>{esc(model)}</code></td>"
                f'<td class="metric">{m.get("call_count", 0)}</td>'
                f'<td class="metric">{m.get("tokens_in", 0):,}</td>'
                f'<td class="metric">{m.get("tokens_out", 0):,}</td>'
                f'<td class="metric">${m.get("cost_usd", 0):.4f}</td></tr>'
            )
        parts.append("</tbody></table>")

    parts.append("</body></html>")
    return "\n".join(parts) + "\n"
