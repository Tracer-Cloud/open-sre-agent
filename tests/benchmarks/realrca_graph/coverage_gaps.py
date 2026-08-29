from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.io import DATASET_DIR, DEFAULT_CURRENT_BEST
from tests.benchmarks.realrca_graph.reports import TriageCase, build_triage_report

EXPECTED_MODALITIES_BY_TYPE: dict[str, tuple[str, ...]] = {
    "HSF": ("trace", "metric"),
    "TDDL": ("sql", "metric"),
    "RDS": ("sql", "metric"),
    "SQL": ("sql", "metric"),
    "Tair": ("trace", "metric"),
    "METAQ": ("metric",),
    "CPU": ("metric",),
    "JVM": ("metric",),
    "异常日志": ("log",),
}

EXPECTED_ROOT_LAYERS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "TDDL": ("database",),
    "RDS": ("database",),
    "SQL": ("database",),
    "Tair": ("cache",),
    "METAQ": ("message_queue",),
    "CPU": ("infrastructure",),
    "JVM": ("runtime", "infrastructure"),
}


@dataclass(frozen=True)
class CoverageGapCase:
    """One case annotated with graph, evidence, verifier, and feedback gaps."""

    case_id: str
    case_suffix: str
    case_type: str
    gap_score: float
    bucket: str
    graph_path: str | None
    baseline_support: float
    baseline_risks: list[str]
    evidence_modalities: list[str]
    top_hypothesis: str
    top_hypothesis_layer: str
    top_hypothesis_modalities: list[str]
    missing_modalities: list[str]
    categories: list[str]
    recommended_actions: list[str]
    probe_count: int
    best_probe_accuracy: float | None
    probe_agents: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoverageGapReport:
    """Aggregate coverage gaps that should drive the next RealRCA iteration."""

    split: str
    baseline_path: str
    graph_roots: list[str]
    case_count: int
    category_counts: dict[str, int]
    bucket_counts: dict[str, int]
    type_counts: dict[str, int]
    best_leaderboard_accuracy: float | None
    cases: list[CoverageGapCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "baseline_path": self.baseline_path,
            "graph_root": self.graph_roots[0] if self.graph_roots else "",
            "graph_roots": list(self.graph_roots),
            "case_count": self.case_count,
            "category_counts": dict(self.category_counts),
            "bucket_counts": dict(self.bucket_counts),
            "type_counts": dict(self.type_counts),
            "best_leaderboard_accuracy": self.best_leaderboard_accuracy,
            "cases": [item.to_dict() for item in self.cases],
        }


def build_coverage_gap_report(
    *,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    graph_roots: Sequence[Path] = (),
    split: str = "test",
    candidate_paths: Sequence[Path] = (),
    dataset_dir: Path = DATASET_DIR,
    case_ids: Sequence[str] = (),
    leaderboard_path: Path | None = None,
    team_name: str = "隐元玩一玩",
) -> CoverageGapReport:
    """Classify evidence-coverage gaps without reading hidden references."""

    triage = build_triage_report(
        baseline_path=baseline_path,
        graph_roots=graph_roots,
        split=split,
        candidate_paths=candidate_paths,
        dataset_dir=dataset_dir,
        case_ids=case_ids,
        leaderboard_path=leaderboard_path,
        team_name=team_name,
    )
    cases = [_gap_case(item, triage.best_leaderboard_accuracy) for item in triage.cases]
    cases.sort(key=lambda item: (-item.gap_score, item.case_type, item.case_id))
    category_counts = Counter(category for item in cases for category in item.categories)
    return CoverageGapReport(
        split=split,
        baseline_path=triage.baseline_path,
        graph_roots=triage.graph_roots,
        case_count=len(cases),
        category_counts=dict(category_counts),
        bucket_counts=triage.bucket_counts,
        type_counts=triage.type_counts,
        best_leaderboard_accuracy=triage.best_leaderboard_accuracy,
        cases=cases,
    )


def render_coverage_gap_markdown(report: CoverageGapReport, *, limit: int = 50) -> str:
    """Render a compact gap report for choosing graph/enrichment work."""

    lines = [
        "# RealRCA Coverage Gaps",
        "",
        f"- split: `{report.split}`",
        f"- cases: `{report.case_count}`",
        f"- baseline: `{report.baseline_path}`",
        f"- graph_roots: `{report.graph_roots}`",
        f"- best_leaderboard_accuracy: `{report.best_leaderboard_accuracy}`",
        f"- buckets: `{report.bucket_counts}`",
        f"- top_categories: `{_top_counts(report.category_counts)}`",
        "",
        "## Top Gaps",
        "",
        "| rank | case | type | score | bucket | support | evidence | missing | categories | action |",
        "| --- | --- | --- | ---: | --- | ---: | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type,
                    f"{item.gap_score:.3f}",
                    item.bucket,
                    f"{item.baseline_support:.4f}",
                    ",".join(item.evidence_modalities) or "-",
                    ",".join(item.missing_modalities) or "-",
                    ",".join(item.categories[:4]).replace("|", "/") or "-",
                    item.recommended_actions[0].replace("|", "/")
                    if item.recommended_actions
                    else "-",
                ]
            )
            + " |"
        )

    lines.extend(["", "## Category Counts", ""])
    for category, count in sorted(
        report.category_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"- `{category}`: {count}")

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
                    f"risks: `{item.baseline_risks}`"
                ),
                (
                    f"- evidence_modalities: `{item.evidence_modalities}`; "
                    f"missing_modalities: `{item.missing_modalities}`"
                ),
                (
                    f"- top_hypothesis: `{item.top_hypothesis}`; layer: "
                    f"`{item.top_hypothesis_layer}`; modalities: `{item.top_hypothesis_modalities}`"
                ),
                (
                    f"- probes: count=`{item.probe_count}` best_accuracy="
                    f"`{item.best_probe_accuracy}` agents=`{item.probe_agents}`"
                ),
                f"- categories: `{item.categories}`",
                f"- recommended_actions: `{item.recommended_actions}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _gap_case(item: TriageCase, best_leaderboard_accuracy: float | None) -> CoverageGapCase:
    evidence_modalities = _modalities_from_counts(item.modality_counts)
    support_modalities = sorted(name for name in item.top_hypothesis_modalities if name != "other")
    observed_modalities = sorted(set(evidence_modalities) | set(support_modalities))
    missing_modalities = _missing_modalities(item.case_type, observed_modalities)
    categories = _categories(
        item,
        evidence_modalities=evidence_modalities,
        support_modalities=support_modalities,
        missing_modalities=missing_modalities,
        best_leaderboard_accuracy=best_leaderboard_accuracy,
    )
    actions = _recommended_actions(
        item,
        evidence_modalities=evidence_modalities,
        missing_modalities=missing_modalities,
        categories=categories,
    )
    return CoverageGapCase(
        case_id=item.case_id,
        case_suffix=item.case_suffix,
        case_type=item.case_type,
        gap_score=_gap_score(item, categories, missing_modalities, best_leaderboard_accuracy),
        bucket=item.bucket,
        graph_path=item.graph_path,
        baseline_support=item.baseline_support,
        baseline_risks=list(item.baseline_risks),
        evidence_modalities=evidence_modalities,
        top_hypothesis=item.top_hypothesis,
        top_hypothesis_layer=item.top_hypothesis_layer,
        top_hypothesis_modalities=support_modalities,
        missing_modalities=missing_modalities,
        categories=categories,
        recommended_actions=actions,
        probe_count=item.probe_count,
        best_probe_accuracy=item.best_probe_accuracy,
        probe_agents=list(item.probe_agents),
    )


def _modalities_from_counts(counts: dict[str, int]) -> list[str]:
    return sorted(name for name, count in counts.items() if count > 0 and name != "other")


def _missing_modalities(case_type: str, evidence_modalities: Sequence[str]) -> list[str]:
    expected = EXPECTED_MODALITIES_BY_TYPE.get(case_type, ())
    available = set(evidence_modalities)
    return [item for item in expected if item not in available]


def _categories(
    item: TriageCase,
    *,
    evidence_modalities: Sequence[str],
    support_modalities: Sequence[str],
    missing_modalities: Sequence[str],
    best_leaderboard_accuracy: float | None,
) -> list[str]:
    categories: list[str] = []
    if item.bucket in {
        "missing_graph",
        "no_graph_hypothesis",
        "unsupported_baseline",
        "weak_baseline",
    }:
        categories.append(item.bucket)
    if item.graph_path is None:
        categories.append("missing_graph_context")
    for modality in missing_modalities:
        categories.append(f"missing_modality:{modality}")
    if item.top_hypothesis and len(support_modalities) < 2:
        categories.append("top_support_single_modality")
    if not item.top_hypothesis:
        categories.append("missing_root_hypothesis")
    expected_layers = EXPECTED_ROOT_LAYERS_BY_TYPE.get(item.case_type, ())
    if (
        expected_layers
        and item.top_hypothesis_layer
        and item.top_hypothesis_layer not in expected_layers
    ):
        categories.append(f"root_layer_mismatch:{item.top_hypothesis_layer}")
    for risk in item.baseline_risks:
        categories.append(f"verifier_risk:{risk}")
    if item.baseline_contract_score < 0.62:
        categories.append("baseline_contract_incomplete")
    if _known_negative_probe(item, best_leaderboard_accuracy):
        categories.append("known_negative_probe")
    if item.best_candidate is None:
        categories.append("no_candidate_pool_improvement")
    elif item.best_candidate.risks:
        categories.append("candidate_blocked_by_verifier")
    if len(evidence_modalities) < 2:
        categories.append("case_evidence_single_modality")
    return _unique(categories)


def _recommended_actions(
    item: TriageCase,
    *,
    evidence_modalities: Sequence[str],
    missing_modalities: Sequence[str],
    categories: Sequence[str],
) -> list[str]:
    actions: list[str] = []
    category_set = set(categories)
    if "missing_graph_context" in category_set:
        actions.append(
            "重建 graph_context；若 snapshot alarm 为空，仅用 live alarm metadata 补 app/time/trace seed。"
        )
    if "missing_root_hypothesis" in category_set:
        actions.append("补 ontology root pattern 或从 trace/log/metric 构造候选根因节点。")
    if "missing_modality:sql" in category_set:
        actions.append(
            "补 TDDL/RDS SQL 证据；优先解析 trace SQL span、慢 SQL 日志和 rds-sql 结果。"
        )
    if "missing_modality:trace" in category_set:
        actions.append(
            "补 trace list/get 证据，明确 provider、consumer、downstream 和 result_code。"
        )
    if "missing_modality:metric" in category_set:
        actions.append("补报警指标同维度 metric series，避免只靠 alarm 或日志描述。")
    if "missing_modality:log" in category_set:
        actions.append(
            "补异常日志或 trace-id 关联日志，提取 exception/code/message 作为 evidence。"
        )
    if "top_support_single_modality" in category_set and not missing_modalities:
        actions.append("把当前 top root 的支撑扩展到第二模态，再考虑生成候选。")
    if any(category.startswith("root_layer_mismatch:") for category in categories):
        actions.append("做人审 root boundary：区分触发点、故障点、放大器和告警症状。")
    if "known_negative_probe" in category_set:
        actions.append("不要重复文本扩写；必须先引入新数据源或更强结构证据。")
    if "baseline_contract_incomplete" in category_set:
        actions.append(
            "当前答案结构不完整；先做 evidence-contract rewrite，再用单 case probe 验证。"
        )
    if not actions and len(evidence_modalities) >= 2:
        actions.append("保持当前答案；只在候选能保留 baseline 关键实体且提高多模态支持时 probe。")
    if not actions:
        actions.append("先补证据覆盖，再运行 verifier/selector。")
    return _unique(actions)


def _gap_score(
    item: TriageCase,
    categories: Sequence[str],
    missing_modalities: Sequence[str],
    best_leaderboard_accuracy: float | None,
) -> float:
    score = item.priority
    score += 4.0 * len(missing_modalities)
    score += 1.5 * sum(1 for category in categories if category.startswith("verifier_risk:"))
    if "missing_graph_context" in categories or "missing_root_hypothesis" in categories:
        score += 8.0
    if "top_support_single_modality" in categories:
        score += 4.0
    if "baseline_contract_incomplete" in categories:
        score += 3.0
    if _known_negative_probe(item, best_leaderboard_accuracy):
        score -= min(18.0, 6.0 * item.probe_count)
    return round(score, 3)


def _known_negative_probe(item: TriageCase, best_leaderboard_accuracy: float | None) -> bool:
    return (
        item.probe_count > 0
        and item.best_probe_accuracy is not None
        and best_leaderboard_accuracy is not None
        and item.best_probe_accuracy <= best_leaderboard_accuracy
    )


def _top_counts(counts: dict[str, int], *, limit: int = 8) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _unique(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
