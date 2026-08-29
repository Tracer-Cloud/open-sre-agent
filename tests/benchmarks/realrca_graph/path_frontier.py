from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.bundle_cache import build_evidence_bundle_cached
from tests.benchmarks.realrca_graph.causal_paths import build_causal_path_report
from tests.benchmarks.realrca_graph.features import clip_text
from tests.benchmarks.realrca_graph.graph_store import resolved_graph_context_paths
from tests.benchmarks.realrca_graph.io import DEFAULT_CURRENT_BEST, load_json, rows_by_case
from tests.benchmarks.realrca_graph.verifier import score_candidate


@dataclass(frozen=True)
class PathFrontierCase:
    """Current-best answer support audited through graph causal paths."""

    case_id: str
    case_suffix: str
    case_type: str
    graph_path: str
    priority_score: float
    categories: list[str]
    baseline_support: float
    best_hypothesis_id: str
    best_hypothesis_label: str
    path_score: float | None
    path_length: int | None
    path_risks: list[str]
    path_nodes: list[str]
    baseline_preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PathFrontierReport:
    """Batch causal-path audit for current-best RealRCA answers."""

    split: str
    baseline_path: str
    graph_roots: list[str]
    case_count: int
    category_counts: dict[str, int]
    cases: list[PathFrontierCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "baseline_path": self.baseline_path,
            "graph_roots": list(self.graph_roots),
            "hidden_test_reference_used": False,
            "case_count": self.case_count,
            "category_counts": dict(self.category_counts),
            "cases": [item.to_dict() for item in self.cases],
        }


def build_path_frontier_report(
    *,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    graph_roots: Sequence[Path] = (),
    split: str = "test",
    case_ids: Sequence[str] = (),
    evidence_limit: int = 32,
    hypothesis_limit: int = 10,
    support_limit: int = 4,
    max_depth: int = 5,
    seed_limit: int = 8,
) -> PathFrontierReport:
    """Rank cases where current-best answer text lacks a short graph path."""

    baseline_rows = rows_by_case(baseline_path, source=baseline_path.stem)
    selected = {item.lower() for item in case_ids}
    cases: list[PathFrontierCase] = []
    for graph_path in resolved_graph_context_paths(graph_roots, split=split):
        case_id = graph_path.parent.name
        suffix = _case_suffix(case_id)
        if selected and case_id.lower() not in selected and suffix not in selected:
            continue
        baseline = baseline_rows.get(case_id)
        if baseline is None:
            continue
        graph_context = load_json(graph_path)
        bundle = build_evidence_bundle_cached(
            graph_path,
            evidence_limit=evidence_limit,
            hypothesis_limit=hypothesis_limit,
            support_limit=support_limit,
        )
        answer_score = score_candidate(baseline, baseline, bundle)
        path_report = build_causal_path_report(
            graph_context,
            bundle,
            max_depth=max_depth,
            seed_limit=seed_limit,
        )
        path_by_hypothesis = {item.hypothesis_id: item for item in path_report.hypotheses}
        path = path_by_hypothesis.get(answer_score.best_hypothesis_id)
        cases.append(
            _frontier_case(
                graph_path=graph_path,
                baseline_preview=clip_text(baseline.diagnosis_output, 240),
                answer_score=answer_score,
                bundle_case_type=bundle.case_type,
                path=path,
            )
        )
    cases.sort(key=lambda item: (-item.priority_score, item.case_type, item.case_id))
    return PathFrontierReport(
        split=split,
        baseline_path=str(baseline_path),
        graph_roots=[str(path) for path in graph_roots],
        case_count=len(cases),
        category_counts=dict(Counter(category for item in cases for category in item.categories)),
        cases=cases,
    )


def render_path_frontier_markdown(report: PathFrontierReport, *, limit: int = 60) -> str:
    """Render a compact current-best path-frontier report."""

    lines = [
        "# RealRCA Path Frontier",
        "",
        f"- split: `{report.split}`",
        f"- baseline: `{report.baseline_path}`",
        "- hidden_test_reference_used: `False`",
        f"- cases: `{report.case_count}`",
        f"- category_counts: `{_top_counts(report.category_counts)}`",
        "",
        "| rank | case | type | priority | support | path | best_hypothesis | risks | categories |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type,
                    f"{item.priority_score:.3f}",
                    f"{item.baseline_support:.3f}",
                    _fmt(item.path_score),
                    f"`{item.best_hypothesis_label}`",
                    ",".join(item.path_risks[:3]) or "-",
                    ",".join(item.categories[:4]) or "-",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Case Notes", ""])
    for item in report.cases[:limit]:
        lines.extend(
            [
                f"### `{item.case_suffix}` {item.case_type}",
                "",
                f"- case_id: `{item.case_id}`",
                f"- graph_path: `{item.graph_path}`",
                f"- priority: `{item.priority_score}` categories: `{item.categories}`",
                (
                    f"- support: `{item.baseline_support}` best_hypothesis=`{item.best_hypothesis_label}` "
                    f"path_score=`{item.path_score}` path_length=`{item.path_length}` risks=`{item.path_risks}`"
                ),
                f"- path_nodes: `{item.path_nodes[:6]}`",
                f"- baseline_preview: {item.baseline_preview}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _frontier_case(
    *,
    graph_path: Path,
    baseline_preview: str,
    answer_score: Any,
    bundle_case_type: str,
    path: Any,
) -> PathFrontierCase:
    path_score = getattr(path, "path_score", None)
    path_length = getattr(path, "path_length", None)
    path_risks = (
        list(getattr(path, "risk_flags", [])) if path is not None else ["missing_path_hypothesis"]
    )
    categories: list[str] = []
    priority = 0.0
    if answer_score.graph_support < 0.7:
        categories.append("low_keyword_support")
        priority += 1.5
    if path is None or path_score is None or path_score <= 0.0:
        categories.append("no_symptom_path")
        priority += 2.5
    elif path_score < 0.45:
        categories.append("weak_symptom_path")
        priority += 1.2
    elif path_score >= 0.8:
        categories.append("strong_symptom_path")
        priority -= 0.6
    if "span_mentions_bridge" in path_risks:
        categories.append("span_mentions_bridge")
        priority += 0.5
    if "high_fanout_bridge" in path_risks:
        categories.append("high_fanout_bridge")
        priority += 0.5
    if not categories:
        categories.append("moderate_symptom_path")
    node_labels = []
    if path is not None:
        node_labels = [f"{node.kind}:{node.label}" for node in path.path_nodes]
    return PathFrontierCase(
        case_id=graph_path.parent.name,
        case_suffix=_case_suffix(graph_path.parent.name),
        case_type=bundle_case_type,
        graph_path=str(graph_path),
        priority_score=round(max(0.0, priority), 3),
        categories=categories,
        baseline_support=answer_score.graph_support,
        best_hypothesis_id=answer_score.best_hypothesis_id,
        best_hypothesis_label=answer_score.best_hypothesis_label,
        path_score=path_score,
        path_length=path_length,
        path_risks=path_risks,
        path_nodes=node_labels,
        baseline_preview=baseline_preview,
    )


def _case_suffix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[-1][-4:].lower()


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def _top_counts(counts: dict[str, int], *, limit: int = 8) -> str:
    return ", ".join(f"{key}={value}" for key, value in Counter(counts).most_common(limit))
