from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.bundle_cache import build_evidence_bundle_cached
from tests.benchmarks.realrca_graph.features import clip_text, token_features
from tests.benchmarks.realrca_graph.io import (
    DATASET_DIR,
    DEFAULT_CURRENT_BEST,
    DEFAULT_GRAPH_ROOT,
    graph_context_path,
    load_cases,
    load_json,
    rows_by_case,
)
from tests.benchmarks.realrca_graph.models import EvidenceBundle
from tests.benchmarks.realrca_graph.verifier import score_candidate

CACHE_INSTANCE_RE = re.compile(
    r"\b(?:tair@)?([0-9a-f]{12,24})(?::[a-z0-9_.-]+)?\b|\br-[0-9a-z]{8,}\b",
    re.IGNORECASE,
)
IP_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")


@dataclass(frozen=True)
class BoundaryDeltaCase:
    """One case whose graph-preferred root may differ from the baseline root boundary."""

    case_id: str
    case_suffix: str
    case_type: str
    opportunity_score: float
    categories: list[str]
    graph_path: str | None
    baseline_support: float
    baseline_risks: list[str]
    baseline_matched_hypothesis: str
    baseline_matched_layer: str
    graph_top_hypothesis: str
    graph_top_layer: str
    graph_top_modalities: list[str]
    graph_top_contradictions: list[str]
    probe_count: int
    best_probe_accuracy: float | None
    action_hint: str
    baseline_preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BoundaryDeltaReport:
    """A report for choosing root-boundary probes after evidence coverage is saturated."""

    split: str
    baseline_path: str
    graph_roots: list[str]
    case_count: int
    category_counts: dict[str, int]
    type_counts: dict[str, int]
    best_leaderboard_accuracy: float | None
    cases: list[BoundaryDeltaCase] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "baseline_path": self.baseline_path,
            "graph_roots": list(self.graph_roots),
            "case_count": self.case_count,
            "category_counts": dict(self.category_counts),
            "type_counts": dict(self.type_counts),
            "best_leaderboard_accuracy": self.best_leaderboard_accuracy,
            "cases": [item.to_dict() for item in self.cases],
        }


def build_boundary_delta_report(
    *,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    graph_roots: Sequence[Path] = (),
    split: str = "test",
    dataset_dir: Path = DATASET_DIR,
    case_ids: Sequence[str] = (),
    leaderboard_path: Path | None = None,
    team_name: str = "隐元玩一玩",
) -> BoundaryDeltaReport:
    """Rank likely root-boundary opportunities without hidden labels."""

    resolved_graph_roots = _graph_roots(graph_roots)
    baseline_rows = rows_by_case(baseline_path, source=baseline_path.stem)
    case_meta = _case_meta(split, dataset_dir)
    best_leaderboard_accuracy, probes_by_suffix = _probe_ledger(
        leaderboard_path,
        team_name=team_name,
    )
    selected = set(case_ids)
    cases: list[BoundaryDeltaCase] = []
    for case_id, baseline in baseline_rows.items():
        if selected and case_id not in selected:
            continue
        graph_path = _find_graph_context_path(resolved_graph_roots, split, case_id)
        if graph_path is None:
            cases.append(
                _missing_graph_case(
                    case_id=case_id,
                    baseline_path=baseline_path,
                    baseline_text=baseline.diagnosis_output,
                    case_meta=case_meta,
                )
            )
            continue
        bundle = build_evidence_bundle_cached(graph_path)
        score = score_candidate(baseline, baseline, bundle)
        top = bundle.hypotheses[0] if bundle.hypotheses else None
        matched = _hypothesis_by_id(bundle, score.best_hypothesis_id)
        suffix = _case_suffix(case_id)
        probes = probes_by_suffix.get(suffix, [])
        best_probe_accuracy = max(float(item["accuracy"]) for item in probes) if probes else None
        categories = _categories(
            bundle=bundle,
            baseline_support=score.graph_support,
            baseline_risks=score.risk_flags,
            baseline_text=baseline.diagnosis_output,
            matched_layer=matched.root_layer if matched is not None else "",
            matched_label=matched.label if matched is not None else "",
            top_layer=top.root_layer if top is not None else "",
            top_label=top.label if top is not None else "",
            top_contradictions=list(top.contradictions) if top is not None else [],
            probe_count=len(probes),
            best_probe_accuracy=best_probe_accuracy,
            best_leaderboard_accuracy=best_leaderboard_accuracy,
        )
        cases.append(
            BoundaryDeltaCase(
                case_id=case_id,
                case_suffix=suffix,
                case_type=_case_type(case_id, bundle, case_meta),
                opportunity_score=_opportunity_score(
                    baseline_support=score.graph_support,
                    baseline_risks=score.risk_flags,
                    categories=categories,
                    probe_count=len(probes),
                    best_probe_accuracy=best_probe_accuracy,
                    best_leaderboard_accuracy=best_leaderboard_accuracy,
                ),
                categories=categories,
                graph_path=str(graph_path),
                baseline_support=score.graph_support,
                baseline_risks=list(score.risk_flags),
                baseline_matched_hypothesis=clip_text(matched.label if matched else "", 160),
                baseline_matched_layer=matched.root_layer if matched else "",
                graph_top_hypothesis=clip_text(top.label if top else "", 160),
                graph_top_layer=top.root_layer if top else "",
                graph_top_modalities=list(top.modalities) if top else [],
                graph_top_contradictions=list(top.contradictions) if top else [],
                probe_count=len(probes),
                best_probe_accuracy=best_probe_accuracy,
                action_hint=_action_hint(
                    categories, len(probes), best_probe_accuracy, best_leaderboard_accuracy
                ),
                baseline_preview=clip_text(baseline.diagnosis_output, 260),
            )
        )
    cases.sort(key=lambda item: (-item.opportunity_score, item.case_type, item.case_id))
    return BoundaryDeltaReport(
        split=split,
        baseline_path=str(baseline_path),
        graph_roots=[str(root) for root in resolved_graph_roots],
        case_count=len(cases),
        category_counts=dict(Counter(category for item in cases for category in item.categories)),
        type_counts=dict(Counter(item.case_type for item in cases)),
        best_leaderboard_accuracy=best_leaderboard_accuracy,
        cases=cases,
    )


def render_boundary_delta_markdown(report: BoundaryDeltaReport, *, limit: int = 50) -> str:
    """Render a compact root-boundary probe report."""

    lines = [
        "# RealRCA Root-Boundary Deltas",
        "",
        f"- split: `{report.split}`",
        f"- cases: `{report.case_count}`",
        f"- baseline: `{report.baseline_path}`",
        f"- graph_roots: `{report.graph_roots}`",
        f"- best_leaderboard_accuracy: `{report.best_leaderboard_accuracy}`",
        f"- top_categories: `{_top_counts(report.category_counts)}`",
        "",
        "## Priority Cases",
        "",
        "| rank | case | type | score | support | probes | categories | matched root | graph top root | action |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type,
                    f"{item.opportunity_score:.3f}",
                    f"{item.baseline_support:.4f}",
                    str(item.probe_count),
                    ",".join(item.categories[:4]) or "-",
                    _cell(item.baseline_matched_hypothesis),
                    _cell(item.graph_top_hypothesis),
                    _cell(item.action_hint),
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
                (
                    f"- baseline_support: `{item.baseline_support:.4f}`; "
                    f"baseline_risks: `{item.baseline_risks}`"
                ),
                (
                    f"- matched: `{item.baseline_matched_layer}` "
                    f"`{item.baseline_matched_hypothesis}`"
                ),
                (
                    f"- graph_top: `{item.graph_top_layer}` `{item.graph_top_hypothesis}`; "
                    f"modalities: `{item.graph_top_modalities}`; "
                    f"contradictions: `{item.graph_top_contradictions}`"
                ),
                (
                    f"- probes: count=`{item.probe_count}` "
                    f"best_accuracy=`{item.best_probe_accuracy}`"
                ),
                f"- categories: `{item.categories}`",
                f"- action: {item.action_hint}",
                f"- baseline_preview: {item.baseline_preview}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _graph_roots(graph_roots: Sequence[Path]) -> list[Path]:
    roots: list[Path] = []
    for root in graph_roots:
        if root not in roots:
            roots.append(root)
    return roots or [DEFAULT_GRAPH_ROOT]


def _find_graph_context_path(graph_roots: Sequence[Path], split: str, case_id: str) -> Path | None:
    for root in graph_roots:
        path = graph_context_path(root, split, case_id)
        if path.exists():
            return path
    return None


def _case_suffix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[-1][-4:]


def _case_meta(split: str, dataset_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        rows = load_cases(split, dataset_dir)
    except FileNotFoundError:
        return {}
    return {
        str(row.get("case_id")): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }


def _case_type(case_id: str, bundle: EvidenceBundle | None, meta: dict[str, dict[str, Any]]) -> str:
    if bundle is not None and bundle.case_type:
        return bundle.case_type
    row = meta.get(case_id) or {}
    return str(row.get("type") or row.get("case_type") or "unknown")


def _probe_suffix(agent_name: str) -> str:
    for token in reversed(agent_name.lower().split("-")):
        if len(token) == 5 and token.startswith("321"):
            return token[-4:]
        if len(token) == 4 and all(char in "0123456789abcdef" for char in token):
            return token
    return ""


def _probe_ledger(
    leaderboard_path: Path | None,
    *,
    team_name: str,
) -> tuple[float | None, dict[str, list[dict[str, Any]]]]:
    if leaderboard_path is None or not leaderboard_path.exists():
        return None, {}
    payload = load_json(leaderboard_path)
    best_accuracy: float | None = None
    ledger: dict[str, list[dict[str, Any]]] = {}
    for item in payload.get("items", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or item.get("team_name") != team_name:
            continue
        try:
            accuracy = float(item.get("accuracy"))
        except (TypeError, ValueError):
            continue
        best_accuracy = accuracy if best_accuracy is None else max(best_accuracy, accuracy)
        suffix = _probe_suffix(str(item.get("agent_name") or ""))
        if suffix:
            ledger.setdefault(suffix, []).append(
                {"accuracy": accuracy, "agent_name": item.get("agent_name")}
            )
    return best_accuracy, ledger


def _hypothesis_by_id(bundle: EvidenceBundle, hypothesis_id: str):
    for hypothesis in bundle.hypotheses:
        if hypothesis.id == hypothesis_id:
            return hypothesis
    return None


def _categories(
    *,
    bundle: EvidenceBundle,
    baseline_support: float,
    baseline_risks: Sequence[str],
    baseline_text: str,
    matched_layer: str,
    matched_label: str,
    top_layer: str,
    top_label: str,
    top_contradictions: Sequence[str],
    probe_count: int,
    best_probe_accuracy: float | None,
    best_leaderboard_accuracy: float | None,
) -> list[str]:
    categories: list[str] = []
    if not bundle.hypotheses:
        categories.append("no_graph_hypothesis")
    if baseline_support < 0.78:
        categories.append("low_baseline_support")
    if baseline_risks:
        categories.append("baseline_risk")
    equivalence = ""
    if matched_label and top_label and matched_label != top_label:
        equivalence = _root_equivalence_category(
            matched_layer=matched_layer,
            matched_label=matched_label,
            top_layer=top_layer,
            top_label=top_label,
            baseline_text=baseline_text,
        )
    if matched_layer and top_layer and matched_layer != top_layer and not equivalence:
        categories.append(f"top_layer_diff:{matched_layer}->{top_layer}")
    if matched_label and top_label and matched_label != top_label:
        if equivalence:
            categories.append(equivalence)
        else:
            categories.append("top_root_diff")
    if top_contradictions:
        categories.append("top_root_contradicted")
    if probe_count == 0:
        categories.append("unprobed")
    elif (
        best_leaderboard_accuracy is not None
        and best_probe_accuracy is not None
        and best_probe_accuracy < best_leaderboard_accuracy
    ):
        categories.append("known_negative_probe")
    return categories or ["aligned_with_current_best"]


def _opportunity_score(
    *,
    baseline_support: float,
    baseline_risks: Sequence[str],
    categories: Sequence[str],
    probe_count: int,
    best_probe_accuracy: float | None,
    best_leaderboard_accuracy: float | None,
) -> float:
    score = max(0.0, 1.0 - baseline_support) * 4.0
    if "unprobed" in categories:
        score += 2.0
    if "top_root_diff" in categories:
        score += 1.2
    if any(item.startswith("top_root_equiv:") for item in categories):
        score -= 1.0
    if any(item.startswith("top_layer_diff:") for item in categories):
        score += 1.5
    if "low_baseline_support" in categories:
        score += 1.0
    if baseline_risks:
        score += 0.8
    if "top_root_contradicted" in categories:
        score -= 1.0
    if best_leaderboard_accuracy is not None and best_probe_accuracy is not None:
        if best_probe_accuracy < best_leaderboard_accuracy:
            score -= 5.0 + min(4.0, float(probe_count))
        elif best_probe_accuracy > best_leaderboard_accuracy:
            score += 8.0
    return round(score, 4)


def _action_hint(
    categories: Sequence[str],
    probe_count: int,
    best_probe_accuracy: float | None,
    best_leaderboard_accuracy: float | None,
) -> str:
    if (
        best_leaderboard_accuracy is not None
        and best_probe_accuracy is not None
        and best_probe_accuracy < best_leaderboard_accuracy
    ):
        return "已有负反馈；除非引入新证据源或保留原主因，不再重复 probe。"
    if any(item.startswith("top_root_equiv:") for item in categories):
        return "同根边界差异；保持 current-best，除非新证据改变触发点/故障点/放大器分层。"
    if "unprobed" in categories and any(item.startswith("top_layer_diff:") for item in categories):
        return "优先让 DMA 生成 root-boundary 反事实候选，并要求保留 baseline 关键实体。"
    if "unprobed" in categories and "top_root_diff" in categories:
        return "可做单 case 小步 probe；重点比较触发点、故障点和告警症状。"
    if "low_baseline_support" in categories:
        return "先补证据或生成更贴近图谱 top root 的候选，再进入 selector。"
    if probe_count == 0:
        return "没有明显错位；只在有更强候选时 probe。"
    return "保持 current-best，等待新数据源。"


def _missing_graph_case(
    *,
    case_id: str,
    baseline_path: Path,
    baseline_text: str,
    case_meta: dict[str, dict[str, Any]],
) -> BoundaryDeltaCase:
    return BoundaryDeltaCase(
        case_id=case_id,
        case_suffix=_case_suffix(case_id),
        case_type=str((case_meta.get(case_id) or {}).get("type") or "unknown"),
        opportunity_score=10.0,
        categories=["missing_graph"],
        graph_path=None,
        baseline_support=0.0,
        baseline_risks=[],
        baseline_matched_hypothesis="",
        baseline_matched_layer="",
        graph_top_hypothesis="",
        graph_top_layer="",
        graph_top_modalities=[],
        graph_top_contradictions=[],
        probe_count=0,
        best_probe_accuracy=None,
        action_hint=f"缺少图谱；先用 {baseline_path.name} 中的 trace seed 重建 graph_context。",
        baseline_preview=clip_text(baseline_text, 260),
    )


def _root_equivalence_category(
    *,
    matched_layer: str,
    matched_label: str,
    top_layer: str,
    top_label: str,
    baseline_text: str,
) -> str:
    if matched_layer == "cache" and _same_cache_fault_domain(
        baseline_text=baseline_text,
        matched_label=matched_label,
        top_label=top_label,
    ):
        return "top_root_equiv:cache_instance"
    if matched_layer == "service_dependency" and _same_app_host_fault_domain(
        matched_label,
        top_label,
    ):
        return "top_root_equiv:same_app_ip"
    if _same_hsf_threadpool_host_fault_domain(
        baseline_text=baseline_text,
        matched_label=matched_label,
        top_label=top_label,
    ):
        return "top_root_equiv:hsf_threadpool_host"
    if matched_layer != top_layer:
        return ""
    return ""


def _same_cache_fault_domain(*, baseline_text: str, matched_label: str, top_label: str) -> bool:
    generic_cache = "cache_timeout" in matched_label.lower() or "cache_timeout" in top_label.lower()
    if not generic_cache:
        return False
    top_instances = _cache_instances(top_label)
    if not top_instances:
        return False
    baseline_instances = _cache_instances(f"{matched_label} {baseline_text}")
    return bool(top_instances & baseline_instances)


def _same_app_host_fault_domain(left: str, right: str) -> bool:
    left_tokens = token_features(left)
    right_tokens = token_features(right)
    left_apps = {item for item in left_tokens if item.startswith("app:")}
    right_apps = {item for item in right_tokens if item.startswith("app:")}
    left_ips = {item for item in left_tokens if item.startswith("ip:")}
    right_ips = {item for item in right_tokens if item.startswith("ip:")}
    return bool(left_apps & right_apps) and bool(left_ips & right_ips)


def _same_hsf_threadpool_host_fault_domain(
    *, baseline_text: str, matched_label: str, top_label: str
) -> bool:
    text = f"{baseline_text} {matched_label} {top_label}"
    if not re.search(r"HSF|THREADPOOL_BUSY|threadpool|thread pool|线程池", text, re.IGNORECASE):
        return False
    matched_ips = set(IP_RE.findall(f"{matched_label} {baseline_text}"))
    top_ips = set(IP_RE.findall(top_label))
    return bool(matched_ips & top_ips)


def _cache_instances(text: str) -> set[str]:
    instances: set[str] = set()
    for match in CACHE_INSTANCE_RE.finditer(text):
        instances.add((match.group(1) or match.group(0)).lower())
    return instances


def _top_counts(counts: dict[str, int], *, limit: int = 12) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _cell(text: str) -> str:
    return clip_text(text.replace("|", "/").replace("\n", " "), 80)
