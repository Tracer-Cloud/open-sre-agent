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
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Paper reference baselines — Wang et al. 2026, "Cloud-OpsBench" Table 4       #
# (arXiv:2603.00468v1), the "Base" (zero-shot) setting over the full 452-case  #
# corpus, single run per case. A@k there is a MEAN over cases (Eq. in §4.2.1), #
# not a median, and a diagnosis counts only on a strict triple match of        #
# <Stage, Component, Root Cause>. These figures are what our headline must be   #
# compared against — and the comparison is only valid for the single-shot,     #
# full-corpus stratum (see headline note).                                     #
# --------------------------------------------------------------------------- #

_PAPER_BASELINE: dict[str, dict[str, float]] = {
    "gpt-4o": {"a1": 0.49, "a3": 0.55, "tcr": 0.99, "cov": 0.78, "steps": 5.67, "iac": 0.27},
    "gpt-5": {"a1": 0.67, "a3": 0.75, "tcr": 0.99, "cov": 0.77, "steps": 5.57, "iac": 0.04},
    "claude-4-sonnet": {"a1": 0.50, "a3": 0.54, "tcr": 0.98, "cov": 0.52, "steps": 4.25, "iac": 0.12},
    "deepseek-v3.2": {"a1": 0.73, "a3": 0.79, "tcr": 0.99, "cov": 0.88, "steps": 10.0, "iac": 0.25},
    "qwen3-235b": {"a1": 0.50, "a3": 0.53, "tcr": 0.96, "cov": 0.67, "steps": 5.34, "iac": 0.22},
    "qwen3-14b": {"a1": 0.34, "a3": 0.43, "tcr": 0.82, "cov": 0.71, "steps": 5.82, "iac": 0.40},
    "qwen3-8b": {"a1": 0.21, "a3": 0.23, "tcr": 0.92, "cov": 0.47, "steps": 5.46, "iac": 0.40},
}

# Paper Table 5 — In-Context Learning (3 retrieved diagnostic traces, NO agent
# framework). The cost-equivalent baseline opensre actually has to beat: a few
# in-context demos lift GPT-4o 0.49 -> 0.70 with no orchestration. Only the
# three models the paper ran under ICL are present.
_PAPER_ICL: dict[str, dict[str, float]] = {
    "gpt-4o": {"a1": 0.70, "a3": 0.75, "tcr": 0.97, "cov": 0.76, "steps": 4.40, "iac": 0.08},
    "qwen3-235b": {"a1": 0.59, "a3": 0.63, "tcr": 0.98, "cov": 0.66, "steps": 3.11, "iac": 0.09},
    "qwen3-14b": {"a1": 0.71, "a3": 0.75, "tcr": 0.99, "cov": 0.86, "steps": 6.29, "iac": 0.29},
}

# Metrics defined identically in the paper (Table 4) — the only set for which a
# head-to-head number against the published baseline is meaningful.
_PAPER_COMPARABLE_METRICS = ["a1", "a3", "tcr", "cov", "steps", "iac"]

# opensre-only instrumentation. Useful as internal diagnostics, but NOT present
# in the paper, so they are reported in a separate panel to avoid implying a
# comparison that doesn't exist.
_OPENSRE_ONLY_METRICS = [
    "partial_a1",
    "partial_a3",
    "object_a1",
    "object_a3",
    "citation_grounding_rate",
    "entity_existence_rate",
    "kubectl_actionability_rate",
]


def _match_paper_row(
    table: dict[str, dict[str, float]], llm: str
) -> dict[str, float] | None:
    """Best-effort match of a run's LLM label to a row in a paper table."""
    key = llm.strip().lower()
    if key in table:
        return table[key]
    for name, row in table.items():
        if name in key or key in name:
            return row
    return None


def _match_paper_baseline(llm: str) -> dict[str, float] | None:
    """Paper Table 4 Base (zero-shot) row for this LLM, if any."""
    return _match_paper_row(_PAPER_BASELINE, llm)


def _match_paper_icl(llm: str) -> dict[str, float] | None:
    """Paper Table 5 ICL row for this LLM, if any (only 3 models exist)."""
    return _match_paper_row(_PAPER_ICL, llm)

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
    provenance = _load_provenance(run_dir / "provenance.json")

    out: dict[str, Path] = {}
    if "markdown" in formats:
        md_path = run_dir / "report.md"
        md_path.write_text(_render_markdown(report, cells, provenance), encoding="utf-8")
        out["markdown"] = md_path
    if "html" in formats:
        html_path = run_dir / "report.html"
        html_path.write_text(_render_html(report, cells, provenance), encoding="utf-8")
        out["html"] = html_path
    return out


def _load_provenance(path: Path) -> dict[str, Any] | None:
    """Optional — provenance is recommended but not required for re-rendering."""
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if isinstance(loaded, dict):
        return loaded
    return None


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


def _scenario_means(cells: list[dict[str, Any]], metric: str) -> list[float]:
    """Collapse per-seed cells to one value per scenario (case_id).

    The benchmark runs multiple seeds per scenario; those repeats are
    *correlated*, not independent samples. Treating each run as an
    independent observation under-states the variance and inflates
    significance. The scenario is the independent unit, so we average the
    seeds within each scenario first and return one value per scenario.
    """
    buckets: dict[str, list[float]] = {}
    for cell in cells:
        value = cell.get("score", {}).get("metrics", {}).get(metric)
        if not isinstance(value, (int, float)):
            continue
        case_id = cell.get("case", {}).get("case_id", "(unknown)")
        buckets.setdefault(case_id, []).append(float(value))
    return [sum(vs) / len(vs) for vs in buckets.values() if vs]


def _mean_with_ci(
    scenario_values: list[float],
    *,
    iters: int = 2000,
    seed: int = 12345,
) -> tuple[float, float, float, int]:
    """Mean + 95% scenario-clustered bootstrap CI.

    Resamples scenarios (not runs) with replacement so the interval reflects
    between-scenario variability — the level at which the paper's A@k is a
    per-case mean. Returns ``(mean, ci_low, ci_high, n_scenarios)``. With
    fewer than 2 scenarios a CI is undefined, so low==high==mean.
    """
    n = len(scenario_values)
    if n == 0:
        return 0.0, 0.0, 0.0, 0
    mean = sum(scenario_values) / n
    if n < 2:
        return mean, mean, mean, n
    rng = random.Random(seed)
    boot_means: list[float] = []
    for _ in range(iters):
        sample_sum = 0.0
        for _ in range(n):
            sample_sum += scenario_values[rng.randrange(n)]
        boot_means.append(sample_sum / n)
    boot_means.sort()
    lo = boot_means[int(0.025 * iters)]
    hi = boot_means[min(iters - 1, int(0.975 * iters))]
    return mean, lo, hi, n


# --------------------------------------------------------------------------- #
# Markdown rendering                                                          #
# --------------------------------------------------------------------------- #


def _render_markdown(
    report: dict[str, Any],
    cells: list[dict[str, Any]],
    provenance: dict[str, Any] | None = None,
) -> str:
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

    # --- Provenance (Mechanism 5: reproducibility) ---
    if provenance is not None:
        lines.extend(_render_provenance_markdown(provenance))

    # --- COI disclosure (Mechanism 10) ---
    coi = (report.get("coi_disclosure") or "").strip()
    if coi:
        lines.append("## Conflict-of-interest disclosure")
        lines.append("")
        for paragraph in coi.split("\n\n"):
            lines.append(paragraph.strip())
            lines.append("")

    # --- Headline panel (paper-comparable: per-LLM MEAN + clustered CI) ---
    lines.append("## Headline — mean per scenario, single-shot (paper-comparable)")
    lines.append("")
    lines.append(
        "Point estimates are **means**, the same aggregation the paper uses "
        "(A@k is a per-case mean, Wang et al. 2026 §4.2.1). CIs are 95% "
        "scenario-clustered bootstrap intervals — the independent unit is the "
        "scenario, not the seed. The `paper` row is the published **Base** "
        "(zero-shot) baseline over the **full 452-case** corpus (Table 4). A "
        "head-to-head claim is only valid when our run is also single-shot and "
        "full-corpus; if the CI overlaps the paper value, the two are "
        "statistically indistinguishable."
    )
    lines.append("")
    by_llm = _cells_by_llm(cells)
    if not by_llm:
        lines.append("_no cells executed_")
    else:
        header = "| LLM | source | n | " + " | ".join(_PAPER_COMPARABLE_METRICS) + " |"
        sep = "|" + "|".join(["---"] * (len(_PAPER_COMPARABLE_METRICS) + 3)) + "|"
        lines.append(header)
        lines.append(sep)
        for llm in sorted(by_llm.keys()):
            llm_cells = by_llm[llm]
            n_scen = len({c.get("case", {}).get("case_id", "?") for c in llm_cells})
            row = [f"`{llm}`", "ours", str(n_scen)]
            for metric in _PAPER_COMPARABLE_METRICS:
                scen_vals = _scenario_means(llm_cells, metric)
                mean, lo, hi, _ = _mean_with_ci(scen_vals)
                if metric in ("a1", "a3") and len(scen_vals) >= 2:
                    row.append(f"{mean:.2f} [{lo:.2f}–{hi:.2f}]")
                else:
                    row.append(f"{mean:.2f}")
            lines.append("| " + " | ".join(row) + " |")
            baseline = _match_paper_baseline(llm)
            if baseline is not None:
                prow = [f"`{llm}`", "paper-Base", "452"]
                for metric in _PAPER_COMPARABLE_METRICS:
                    val = baseline.get(metric)
                    prow.append(f"{val:.2f}" if isinstance(val, (int, float)) else "—")
                lines.append("| " + " | ".join(prow) + " |")
            icl = _match_paper_icl(llm)
            if icl is not None:
                irow = [f"`{llm}`", "paper-ICL", "452"]
                for metric in _PAPER_COMPARABLE_METRICS:
                    val = icl.get(metric)
                    irow.append(f"{val:.2f}" if isinstance(val, (int, float)) else "—")
                lines.append("| " + " | ".join(irow) + " |")
    lines.append("")
    lines.append(
        "_`paper-Base` = zero-shot agent (Table 4). `paper-ICL` = 3 retrieved "
        "in-context traces, **no agent framework** (Table 5) — the "
        "cost-equivalent baseline opensre must beat. ICL exists only for the "
        "three models the paper ran it on._"
    )

    # --- opensre-only diagnostics (NOT in the paper, NOT comparable) ---
    if by_llm:
        present = [
            m
            for m in _OPENSRE_ONLY_METRICS
            if any(_per_cell_metric(cs, m) for cs in by_llm.values())
        ]
        if present:
            lines.append("### opensre-only diagnostics (not in the paper — do not compare)")
            lines.append("")
            lines.append(
                "_These metrics are opensre instrumentation with no published "
                "counterpart. `partial_*` relaxes the triple match; `object_*` "
                "scores component localization alone; the `*_rate` metrics are "
                "heuristic validity probes. Means shown for internal tracking only._"
            )
            lines.append("")
            header = "| LLM | " + " | ".join(present) + " |"
            sep = "|" + "|".join(["---"] * (len(present) + 1)) + "|"
            lines.append(header)
            lines.append(sep)
            for llm in sorted(by_llm.keys()):
                llm_cells = by_llm[llm]
                row = [f"`{llm}`"]
                for metric in present:
                    scen_vals = _scenario_means(llm_cells, metric)
                    mean, _, _, _ = _mean_with_ci(scen_vals)
                    row.append(f"{mean:.2f}")
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")

    # --- Per-stratum × per-LLM detail (Mechanism 4) ---
    lines.append("## Per-stratum × per-LLM (medians — distributional view)")
    lines.append("")
    lines.append(
        "These are **medians** across seeds (a robustness cross-check, not the "
        "headline). Stratum semantics:\n"
        "- `all` / `seen-shape` / `unseen-shape` / `held-out` / `optimize`: "
        "single-shot strata — each seed is one independent draw.\n"
        "- `consistency-selected`: **best-of-N** — the adapter picks the most "
        "self-consistent of the repeated runs per scenario. This is an "
        "*optimistic* selection and is **NOT comparable** to the paper's "
        "single-shot Table 4 baselines; report it separately and never as the "
        "headline."
    )
    lines.append("")
    reported_metrics = report.get("reported_metrics", [])
    per_stratum = report.get("per_stratum", {})
    for stratum in sorted(per_stratum.keys()):
        label = (
            " — best-of-N, optimistic, not paper-comparable"
            if stratum == "consistency-selected"
            else ""
        )
        lines.append(f"### {stratum}{label}")
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


def _render_html(
    report: dict[str, Any],
    cells: list[dict[str, Any]],
    provenance: dict[str, Any] | None = None,
) -> str:
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

    # Provenance section (Mechanism 5)
    if provenance is not None:
        parts.extend(_render_provenance_html(provenance, esc))

    # COI
    coi = (report.get("coi_disclosure") or "").strip()
    if coi:
        parts.append("<h2>Conflict-of-interest disclosure</h2>")
        parts.append('<div class="callout coi">')
        for paragraph in coi.split("\n\n"):
            parts.append(f"<p>{esc(paragraph.strip())}</p>")
        parts.append("</div>")

    # Headline panel — paper-comparable mean + scenario-clustered CI
    parts.append("<h2>Headline — mean per scenario, single-shot (paper-comparable)</h2>")
    parts.append(
        '<div class="callout"><p>Point estimates are <strong>means</strong> '
        "(matching the paper, where A@k is a per-case mean). CIs are 95% "
        "scenario-clustered bootstrap intervals. The <code>paper</code> row is "
        "the published <strong>Base</strong> baseline over the full 452-case "
        "corpus (Wang et al. 2026, Table 4). A head-to-head claim is only valid "
        "when our run is single-shot and full-corpus; if the CI overlaps the "
        "paper value, the two are statistically indistinguishable.</p></div>"
    )
    by_llm = _cells_by_llm(cells)
    if not by_llm:
        parts.append("<p><em>no cells executed</em></p>")
    else:
        parts.append("<table><thead><tr><th>LLM</th><th>source</th><th>n</th>")
        for m in _PAPER_COMPARABLE_METRICS:
            parts.append(f"<th>{esc(m)}</th>")
        parts.append("</tr></thead><tbody>")
        for llm in sorted(by_llm.keys()):
            llm_cells = by_llm[llm]
            n_scen = len({c.get("case", {}).get("case_id", "?") for c in llm_cells})
            parts.append(
                f'<tr><td><code>{esc(llm)}</code></td>'
                f'<td>ours</td><td class="metric">{n_scen}</td>'
            )
            for m in _PAPER_COMPARABLE_METRICS:
                scen_vals = _scenario_means(llm_cells, m)
                mean, lo, hi, _ = _mean_with_ci(scen_vals)
                if m in ("a1", "a3") and len(scen_vals) >= 2:
                    cell_txt = f"{mean:.2f}<br><small>[{lo:.2f}–{hi:.2f}]</small>"
                else:
                    cell_txt = f"{mean:.2f}"
                parts.append(f'<td class="metric">{cell_txt}</td>')
            parts.append("</tr>")
            baseline = _match_paper_baseline(llm)
            if baseline is not None:
                parts.append(
                    f'<tr><td><code>{esc(llm)}</code></td>'
                    '<td><span class="pill">paper-Base</span></td>'
                    '<td class="metric">452</td>'
                )
                for m in _PAPER_COMPARABLE_METRICS:
                    val = baseline.get(m)
                    txt = f"{val:.2f}" if isinstance(val, (int, float)) else "—"
                    parts.append(f'<td class="metric">{txt}</td>')
                parts.append("</tr>")
            icl = _match_paper_icl(llm)
            if icl is not None:
                parts.append(
                    f'<tr><td><code>{esc(llm)}</code></td>'
                    '<td><span class="pill warn">paper-ICL</span></td>'
                    '<td class="metric">452</td>'
                )
                for m in _PAPER_COMPARABLE_METRICS:
                    val = icl.get(m)
                    txt = f"{val:.2f}" if isinstance(val, (int, float)) else "—"
                    parts.append(f'<td class="metric">{txt}</td>')
                parts.append("</tr>")
        parts.append("</tbody></table>")
        parts.append(
            '<p><small><code>paper-Base</code> = zero-shot agent (Table 4). '
            "<code>paper-ICL</code> = 3 retrieved in-context traces, <strong>no "
            "agent framework</strong> (Table 5) — the cost-equivalent baseline "
            "opensre must beat. ICL exists only for the three models the paper "
            "ran it on.</small></p>"
        )

        # opensre-only diagnostics (segregated — not paper-comparable)
        present = [
            m
            for m in _OPENSRE_ONLY_METRICS
            if any(_per_cell_metric(cs, m) for cs in by_llm.values())
        ]
        if present:
            parts.append("<h3>opensre-only diagnostics (not in the paper — do not compare)</h3>")
            parts.append("<table><thead><tr><th>LLM</th>")
            for m in present:
                parts.append(f"<th>{esc(m)}</th>")
            parts.append("</tr></thead><tbody>")
            for llm in sorted(by_llm.keys()):
                llm_cells = by_llm[llm]
                parts.append(f"<tr><td><code>{esc(llm)}</code></td>")
                for m in present:
                    scen_vals = _scenario_means(llm_cells, m)
                    mean, _, _, _ = _mean_with_ci(scen_vals)
                    parts.append(f'<td class="metric">{mean:.2f}</td>')
                parts.append("</tr>")
            parts.append("</tbody></table>")

    # Per-stratum × per-LLM
    parts.append("<h2>Per-stratum × per-LLM (medians — distributional view)</h2>")
    parts.append(
        '<div class="callout"><p>These are <strong>medians</strong> across '
        "seeds (a robustness cross-check, not the headline). "
        "<code>all</code>/<code>seen-shape</code>/<code>unseen-shape</code>/"
        "<code>held-out</code>/<code>optimize</code> are single-shot strata. "
        "<code>consistency-selected</code> is <strong>best-of-N</strong> — an "
        "optimistic selection that is <strong>not comparable</strong> to the "
        "paper's single-shot baselines.</p></div>"
    )
    reported_metrics = report.get("reported_metrics", [])
    for stratum in sorted(report.get("per_stratum", {}).keys()):
        label = (
            " — best-of-N, optimistic, not paper-comparable"
            if stratum == "consistency-selected"
            else ""
        )
        parts.append(f"<h3>{esc(stratum)}{esc(label)}</h3>")
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


# --------------------------------------------------------------------------- #
# Provenance renderers — surface "what exact code + config + env ran"          #
# --------------------------------------------------------------------------- #


def _render_provenance_markdown(prov: dict[str, Any]) -> list[str]:
    """Markdown section with the highest-leverage provenance fields.

    Full content (config YAML, pre-reg YAML, full env) stays in
    ``provenance.json`` — the report just summarizes so reviewers know what
    to look for. Keep this short.
    """
    lines: list[str] = []
    code = prov.get("code", {})
    env = prov.get("environment", {})
    dataset = prov.get("dataset", {})
    config_section = prov.get("config", {})
    pre_reg = prov.get("pre_registration", {})

    lines.append("## Provenance (Mechanism 5: reproducibility)")
    lines.append("")
    dirty_marker = " **(DIRTY — uncommitted changes)**" if code.get("opensre_dirty") else ""
    lines.append(
        f"- **Code**: `{code.get('opensre_short_sha', '?')}` on "
        f"`{code.get('opensre_branch', '?')}`{dirty_marker}"
    )
    if code.get("opensre_dirty") and code.get("opensre_changed_files"):
        changed = code["opensre_changed_files"]
        files_str = ", ".join(f"`{f}`" for f in changed[:5])
        suffix = f" (+{len(changed) - 5} more)" if len(changed) > 5 else ""
        lines.append(f"  - Changed files: {files_str}{suffix}")
    if config_section.get("path"):
        lines.append(
            f"- **Config**: `{config_section['path']}` "
            f"(sha256 `{(config_section.get('sha256') or '?')[:12]}…`)"
        )
    if pre_reg.get("path"):
        lines.append(
            f"- **Pre-registration**: `{pre_reg['path']}` "
            f"(sha256 `{(pre_reg.get('sha256') or '?')[:12]}…`)"
        )
    if dataset.get("hf_dataset"):
        rev = dataset.get("hf_revision") or "(unpinned)"
        lines.append(f"- **Dataset**: {dataset['hf_dataset']} @ `{rev}`")
    lines.append(
        f"- **Python**: {env.get('python_version', '?')} "
        f"({env.get('python_implementation', '?')}) on {env.get('platform', '?')}"
    )
    key_packages = env.get("key_packages", {})
    if key_packages:
        pkg_str = ", ".join(
            f"{name} {version}" for name, version in sorted(key_packages.items()) if version
        )
        if pkg_str:
            lines.append(f"- **Key packages**: {pkg_str}")
    lines.append("")
    lines.append(
        "_Full provenance — config + pre-registration contents, every package "
        "version, allowlisted env vars — lives in `provenance.json` in this "
        "run directory._"
    )
    lines.append("")
    return lines


def _render_provenance_html(prov: dict[str, Any], esc: Any) -> list[str]:
    code = prov.get("code", {})
    env = prov.get("environment", {})
    dataset = prov.get("dataset", {})
    config_section = prov.get("config", {})
    pre_reg = prov.get("pre_registration", {})

    parts: list[str] = []
    parts.append("<h2>Provenance (Mechanism 5: reproducibility)</h2>")
    parts.append('<dl class="meta">')

    dirty_pill = ' <span class="pill bad">DIRTY</span>' if code.get("opensre_dirty") else ""
    parts.append(
        f"<dt>Code</dt><dd><code>{esc(code.get('opensre_short_sha', '?'))}</code> "
        f"on <code>{esc(code.get('opensre_branch', '?'))}</code>{dirty_pill}</dd>"
    )

    if code.get("opensre_dirty") and code.get("opensre_changed_files"):
        changed = code["opensre_changed_files"]
        files_html = ", ".join(f"<code>{esc(f)}</code>" for f in changed[:5])
        suffix = f" (+{len(changed) - 5} more)" if len(changed) > 5 else ""
        parts.append(f"<dt>Changed files</dt><dd>{files_html}{esc(suffix)}</dd>")

    if config_section.get("path"):
        sha = (config_section.get("sha256") or "?")[:12]
        parts.append(
            f"<dt>Config</dt><dd><code>{esc(config_section['path'])}</code> "
            f"<small>(sha256 <code>{esc(sha)}…</code>)</small></dd>"
        )
    if pre_reg.get("path"):
        sha = (pre_reg.get("sha256") or "?")[:12]
        parts.append(
            f"<dt>Pre-registration</dt><dd><code>{esc(pre_reg['path'])}</code> "
            f"<small>(sha256 <code>{esc(sha)}…</code>)</small></dd>"
        )
    if dataset.get("hf_dataset"):
        rev = dataset.get("hf_revision") or "(unpinned)"
        parts.append(
            f"<dt>Dataset</dt><dd>{esc(dataset['hf_dataset'])} @ <code>{esc(rev)}</code></dd>"
        )
    parts.append(
        f"<dt>Python</dt><dd>{esc(env.get('python_version', '?'))} "
        f"({esc(env.get('python_implementation', '?'))}) on "
        f"{esc(env.get('platform', '?'))}</dd>"
    )
    key_packages = env.get("key_packages", {})
    pkg_items = sorted((n, v) for n, v in key_packages.items() if v)
    if pkg_items:
        pkg_str = ", ".join(f"{esc(n)} {esc(v)}" for n, v in pkg_items)
        parts.append(f"<dt>Key packages</dt><dd>{pkg_str}</dd>")
    parts.append("</dl>")
    parts.append(
        "<p><small>Full provenance — config + pre-registration contents, "
        "every package version, allowlisted env vars — lives in "
        "<code>provenance.json</code> in this run directory.</small></p>"
    )
    return parts
