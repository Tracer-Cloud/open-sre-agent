from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.bundle_cache import build_evidence_bundle_cached
from tests.benchmarks.realrca_graph.features import clip_text
from tests.benchmarks.realrca_graph.io import (
    DATASET_DIR,
    DEFAULT_GRAPH_ROOT,
    graph_context_path,
    load_cases,
    load_json,
    rows_by_case,
)
from tests.benchmarks.realrca_graph.models import CandidateAnswer, CandidateScore, EvidenceBundle
from tests.benchmarks.realrca_graph.probe_feedback import CaseProbeFeedback, ProbeFeedbackLedger
from tests.benchmarks.realrca_graph.verifier import score_candidate


@dataclass(frozen=True)
class CandidateOpportunity:
    """A replacement candidate scored against the same graph bundle as the baseline."""

    source: str
    support: float
    support_delta: float
    contract_score: float
    contract_flags: list[str]
    novelty: float
    risks: list[str]
    answer_preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TriageCase:
    """One ranked case in the graph/ontology failure-analysis report."""

    case_id: str
    case_suffix: str
    case_type: str
    priority: float
    bucket: str
    action_hint: str
    graph_path: str | None
    baseline_support: float
    baseline_contract_score: float
    baseline_contract_flags: list[str]
    baseline_risks: list[str]
    baseline_preview: str
    modality_counts: dict[str, int]
    top_hypothesis: str
    top_hypothesis_layer: str
    top_hypothesis_modalities: list[str]
    top_hypothesis_contradictions: list[str]
    best_candidate: CandidateOpportunity | None
    probe_count: int = 0
    best_probe_accuracy: float | None = None
    latest_probe_accuracy: float | None = None
    probe_agents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["best_candidate"] = (
            self.best_candidate.to_dict() if self.best_candidate is not None else None
        )
        return payload


@dataclass(frozen=True)
class TriageReport:
    """A deterministic report for choosing the next RealRCA probes."""

    split: str
    baseline_path: str
    graph_roots: list[str]
    case_count: int
    bucket_counts: dict[str, int]
    type_counts: dict[str, int]
    best_leaderboard_accuracy: float | None
    cases: list[TriageCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "baseline_path": self.baseline_path,
            "graph_root": self.graph_roots[0] if self.graph_roots else "",
            "graph_roots": list(self.graph_roots),
            "case_count": self.case_count,
            "bucket_counts": dict(self.bucket_counts),
            "type_counts": dict(self.type_counts),
            "best_leaderboard_accuracy": self.best_leaderboard_accuracy,
            "cases": [item.to_dict() for item in self.cases],
        }


def _case_suffix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[-1][-4:]


def _normalize_graph_roots(
    graph_root: Path | None,
    graph_roots: Sequence[Path],
) -> list[Path]:
    roots: list[Path] = []
    for root in graph_roots:
        if root not in roots:
            roots.append(root)
    if graph_root is not None and graph_root not in roots:
        roots.append(graph_root)
    return roots or [DEFAULT_GRAPH_ROOT]


def _find_graph_context_path(graph_roots: Sequence[Path], split: str, case_id: str) -> Path | None:
    for root in graph_roots:
        path = graph_context_path(root, split, case_id)
        if path.exists():
            return path
    return None


def _probe_suffix(agent_name: str) -> str:
    for token in reversed(agent_name.lower().split("-")):
        if len(token) == 5 and token.startswith("321"):
            return token[-4:]
        if len(token) == 4 and all(char in "0123456789abcdef" for char in token):
            return token
    return ""


def _candidate_pool(paths: Sequence[Path]) -> dict[str, list[CandidateAnswer]]:
    candidates_by_case: dict[str, list[CandidateAnswer]] = {}
    for path in paths:
        if not path.exists():
            continue
        for case_id, row in rows_by_case(path, source=path.stem).items():
            candidates_by_case.setdefault(case_id, []).append(row)
    return candidates_by_case


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


def _probe_ledger(
    leaderboard_path: Path | None,
    *,
    team_name: str,
) -> tuple[float | None, dict[str, list[dict[str, Any]]]]:
    if leaderboard_path is None or not leaderboard_path.exists():
        return None, {}
    payload = load_json(leaderboard_path)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    best_accuracy: float | None = None
    ledger: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("team_name") != team_name:
            continue
        try:
            accuracy = float(item.get("accuracy"))
        except (TypeError, ValueError):
            continue
        best_accuracy = accuracy if best_accuracy is None else max(best_accuracy, accuracy)
        agent_name = str(item.get("agent_name") or "")
        suffix = _probe_suffix(agent_name)
        if not suffix:
            continue
        ledger.setdefault(suffix, []).append(
            {
                "agent_name": agent_name,
                "accuracy": accuracy,
                "coverage": item.get("coverage"),
                "submitted_at": item.get("submitted_at"),
            }
        )
    for records in ledger.values():
        records.sort(key=lambda item: str(item.get("submitted_at") or ""), reverse=True)
    return best_accuracy, ledger


def _case_type(case_id: str, bundle: EvidenceBundle | None, meta: dict[str, dict[str, Any]]) -> str:
    if bundle is not None and bundle.case_type:
        return bundle.case_type
    row = meta.get(case_id) or {}
    return str(row.get("type") or row.get("case_type") or "unknown")


def _modality_counts(bundle: EvidenceBundle) -> dict[str, int]:
    counts = Counter(item.modality for item in bundle.evidence)
    return dict(sorted(counts.items()))


def _top_hypothesis(bundle: EvidenceBundle) -> tuple[str, str, list[str], list[str]]:
    if not bundle.hypotheses:
        return "", "", [], []
    top = bundle.hypotheses[0]
    return top.label, top.root_layer, list(top.modalities), list(top.contradictions)


def _bucket(score: CandidateScore | None, bundle: EvidenceBundle | None) -> str:
    if bundle is None:
        return "missing_graph"
    if not bundle.hypotheses:
        return "no_graph_hypothesis"
    if score is None:
        return "unscored"
    risks = set(score.risk_flags)
    if "no_hypothesis_overlap" in risks:
        return "no_hypothesis_overlap"
    if score.graph_support < 0.4:
        return "unsupported_baseline"
    if score.graph_support < 0.58:
        return "weak_baseline"
    if score.risk_flags:
        return "risky_baseline"
    return "supported_baseline"


def _best_candidate(
    *,
    baseline: CandidateAnswer,
    bundle: EvidenceBundle,
    candidates: Sequence[CandidateAnswer],
    probe_feedback: CaseProbeFeedback | None = None,
) -> CandidateOpportunity | None:
    scored: list[tuple[CandidateScore, CandidateAnswer]] = []
    baseline_score = score_candidate(baseline, baseline, bundle)
    for candidate in candidates:
        if candidate.source == baseline.source:
            continue
        if _same_answer(candidate, baseline):
            continue
        score = score_candidate(candidate, baseline, bundle, probe_feedback=probe_feedback)
        scored.append((score, candidate))
    if not scored:
        return None
    score, answer = sorted(
        scored,
        key=lambda item: (
            -item[0].graph_support,
            len(item[0].risk_flags),
            item[0].novelty,
            item[0].source,
        ),
    )[0]
    return CandidateOpportunity(
        source=score.source,
        support=score.graph_support,
        support_delta=round(score.graph_support - baseline_score.graph_support, 4),
        contract_score=score.answer_contract_score,
        contract_flags=list(score.contract_flags),
        novelty=score.novelty,
        risks=list(score.risk_flags),
        answer_preview=clip_text(answer.diagnosis_output, 180),
    )


def _same_answer(left: CandidateAnswer, right: CandidateAnswer) -> bool:
    return (
        left.trace_id.strip() == right.trace_id.strip()
        and left.diagnosis_output.strip() == right.diagnosis_output.strip()
    )


def _action_hint(
    *,
    case_type: str,
    bucket: str,
    modality_counts: dict[str, int],
    best_candidate: CandidateOpportunity | None,
    probe_count: int,
    best_probe_accuracy: float | None,
    best_leaderboard_accuracy: float | None,
) -> str:
    if (
        probe_count
        and best_probe_accuracy is not None
        and best_leaderboard_accuracy is not None
        and best_probe_accuracy <= best_leaderboard_accuracy
    ):
        return "已 probe 且未超过当前最好分，除非新增证据否则跳过。"
    if bucket == "missing_graph":
        return "先补 graph_context，再判断候选。"
    if bucket == "no_graph_hypothesis":
        return "图谱没有形成候选根因，优先补实体抽取或候选生成。"
    if case_type in {"TDDL", "RDS", "SQL"} and modality_counts.get("sql", 0) == 0:
        return "补 `sf diagnose rds-sql`/慢 SQL 证据后再 probe。"
    if case_type == "HSF" and modality_counts.get("trace", 0) == 0:
        return "补 trace 证据，确认上游 provider 还是本机消费侧。"
    if best_candidate and best_candidate.support_delta >= 0.12 and not best_candidate.risks:
        return "候选明显强于当前答案，适合单 case 提交 probe。"
    if best_candidate and best_candidate.support_delta >= 0.12:
        return "候选支持更高但有风险，先人工读 evidence bundle。"
    if bucket in {"unsupported_baseline", "weak_baseline"}:
        return "当前答案图谱支持弱，优先做 evidence bundle 人审。"
    return "保持当前答案，等待更强候选或新增证据。"


def _priority(
    *,
    bucket: str,
    score: CandidateScore | None,
    modality_counts: dict[str, int],
    best_candidate: CandidateOpportunity | None,
    probe_count: int,
    best_probe_accuracy: float | None,
    best_leaderboard_accuracy: float | None,
) -> float:
    priority = 0.0
    bucket_weights = {
        "missing_graph": 35.0,
        "no_graph_hypothesis": 32.0,
        "no_hypothesis_overlap": 30.0,
        "unsupported_baseline": 26.0,
        "weak_baseline": 20.0,
        "risky_baseline": 12.0,
        "supported_baseline": 0.0,
    }
    priority += bucket_weights.get(bucket, 8.0)
    if score is not None:
        priority += max(0.0, 0.8 - score.graph_support) * 25.0
        priority += 4.0 * len(score.risk_flags)
    if len([name for name, count in modality_counts.items() if name != "other" and count > 0]) < 2:
        priority += 6.0
    if best_candidate is not None:
        priority += max(0.0, best_candidate.support_delta) * 18.0
        if not best_candidate.risks and best_candidate.support_delta >= 0.08:
            priority += 8.0
        if "unsupported_high_novelty" in best_candidate.risks:
            priority -= 4.0
    if (
        probe_count
        and best_probe_accuracy is not None
        and best_leaderboard_accuracy is not None
        and best_probe_accuracy <= best_leaderboard_accuracy
    ):
        priority -= min(20.0, 6.0 * probe_count)
    return round(priority, 3)


def build_triage_report(
    *,
    baseline_path: Path,
    graph_root: Path | None = None,
    graph_roots: Sequence[Path] = (),
    split: str = "test",
    candidate_paths: Sequence[Path] = (),
    dataset_dir: Path = DATASET_DIR,
    case_ids: Sequence[str] = (),
    leaderboard_path: Path | None = None,
    team_name: str = "隐元玩一玩",
) -> TriageReport:
    """Rank cases by graph support, risk, and candidate opportunity."""

    resolved_graph_roots = _normalize_graph_roots(graph_root, graph_roots)
    baseline_rows = rows_by_case(baseline_path, source=baseline_path.stem)
    candidates_by_case = _candidate_pool(candidate_paths)
    case_meta = _case_meta(split, dataset_dir)
    best_leaderboard_accuracy, probe_ledger = _probe_ledger(leaderboard_path, team_name=team_name)
    feedback_ledger = None
    if leaderboard_path is not None and leaderboard_path.exists():
        payload = load_json(leaderboard_path)
        if isinstance(payload, dict):
            feedback_ledger = ProbeFeedbackLedger.from_leaderboard(payload, team_name=team_name)
    selected = set(case_ids)
    cases: list[TriageCase] = []
    for case_id, baseline in baseline_rows.items():
        if selected and case_id not in selected:
            continue
        graph_path = _find_graph_context_path(resolved_graph_roots, split, case_id)
        bundle: EvidenceBundle | None = None
        baseline_score: CandidateScore | None = None
        modality_counts: dict[str, int] = {}
        top_label = ""
        top_layer = ""
        top_modalities: list[str] = []
        top_contradictions: list[str] = []
        if graph_path is not None:
            bundle = build_evidence_bundle_cached(graph_path)
            baseline_score = score_candidate(baseline, baseline, bundle)
            modality_counts = _modality_counts(bundle)
            top_label, top_layer, top_modalities, top_contradictions = _top_hypothesis(bundle)
        case_type = _case_type(case_id, bundle, case_meta)
        suffix = _case_suffix(case_id)
        probe_feedback = feedback_ledger.cases.get(suffix) if feedback_ledger is not None else None
        best_candidate = (
            _best_candidate(
                baseline=baseline,
                bundle=bundle,
                candidates=candidates_by_case.get(case_id, []),
                probe_feedback=probe_feedback,
            )
            if bundle is not None
            else None
        )
        probe_records = probe_ledger.get(suffix, [])
        probe_accuracies = [
            float(item["accuracy"])
            for item in probe_records
            if isinstance(item.get("accuracy"), float)
        ]
        best_probe_accuracy = max(probe_accuracies) if probe_accuracies else None
        latest_probe_accuracy = probe_accuracies[0] if probe_accuracies else None
        bucket = _bucket(baseline_score, bundle)
        cases.append(
            TriageCase(
                case_id=case_id,
                case_suffix=suffix,
                case_type=case_type,
                priority=_priority(
                    bucket=bucket,
                    score=baseline_score,
                    modality_counts=modality_counts,
                    best_candidate=best_candidate,
                    probe_count=len(probe_records),
                    best_probe_accuracy=best_probe_accuracy,
                    best_leaderboard_accuracy=best_leaderboard_accuracy,
                ),
                bucket=bucket,
                action_hint=_action_hint(
                    case_type=case_type,
                    bucket=bucket,
                    modality_counts=modality_counts,
                    best_candidate=best_candidate,
                    probe_count=len(probe_records),
                    best_probe_accuracy=best_probe_accuracy,
                    best_leaderboard_accuracy=best_leaderboard_accuracy,
                ),
                graph_path=str(graph_path) if graph_path is not None else None,
                baseline_support=baseline_score.graph_support
                if baseline_score is not None
                else 0.0,
                baseline_contract_score=(
                    baseline_score.answer_contract_score if baseline_score is not None else 0.0
                ),
                baseline_contract_flags=(
                    list(baseline_score.contract_flags) if baseline_score is not None else []
                ),
                baseline_risks=list(baseline_score.risk_flags)
                if baseline_score is not None
                else [],
                baseline_preview=clip_text(baseline.diagnosis_output, 220),
                modality_counts=modality_counts,
                top_hypothesis=clip_text(top_label, 140),
                top_hypothesis_layer=top_layer,
                top_hypothesis_modalities=top_modalities,
                top_hypothesis_contradictions=top_contradictions,
                best_candidate=best_candidate,
                probe_count=len(probe_records),
                best_probe_accuracy=best_probe_accuracy,
                latest_probe_accuracy=latest_probe_accuracy,
                probe_agents=[str(item.get("agent_name") or "") for item in probe_records[:5]],
            )
        )
    cases.sort(key=lambda item: (-item.priority, item.case_type, item.case_id))
    return TriageReport(
        split=split,
        baseline_path=str(baseline_path),
        graph_roots=[str(root) for root in resolved_graph_roots],
        case_count=len(cases),
        bucket_counts=dict(Counter(item.bucket for item in cases)),
        type_counts=dict(Counter(item.case_type for item in cases)),
        best_leaderboard_accuracy=best_leaderboard_accuracy,
        cases=cases,
    )


def render_triage_markdown(report: TriageReport, *, limit: int = 40) -> str:
    """Render a compact, source-backed report for human probe selection."""

    lines = [
        "# RealRCA Graph/Ontology Triage",
        "",
        f"- split: `{report.split}`",
        f"- cases: `{report.case_count}`",
        f"- baseline: `{report.baseline_path}`",
        f"- graph_roots: `{report.graph_roots}`",
        f"- buckets: `{report.bucket_counts}`",
        f"- types: `{report.type_counts}`",
        f"- best_leaderboard_accuracy: `{report.best_leaderboard_accuracy}`",
        "",
        "## Priority Cases",
        "",
        "| rank | case | type | priority | bucket | support | probes | best candidate | hint |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        best = ""
        if item.best_candidate is not None:
            best = (
                f"{item.best_candidate.source} "
                f"delta={item.best_candidate.support_delta} "
                f"risk={','.join(item.best_candidate.risks) or 'none'}"
            )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type,
                    f"{item.priority:.3f}",
                    item.bucket,
                    f"{item.baseline_support:.4f}",
                    str(item.probe_count),
                    best or "-",
                    item.action_hint.replace("|", "/"),
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
                    f"contract: `{item.baseline_contract_score:.4f}`; "
                    f"risks: `{item.baseline_risks}`; contract_flags: `{item.baseline_contract_flags}`"
                ),
                (
                    f"- probes: count=`{item.probe_count}` best_accuracy=`{item.best_probe_accuracy}` "
                    f"latest_accuracy=`{item.latest_probe_accuracy}` agents=`{item.probe_agents}`"
                ),
                f"- modalities: `{item.modality_counts}`",
                f"- top_hypothesis: `{item.top_hypothesis}`",
                f"- top_layer: `{item.top_hypothesis_layer}`; top_modalities: `{item.top_hypothesis_modalities}`",
                f"- contradictions: `{item.top_hypothesis_contradictions}`",
                f"- action: {item.action_hint}",
                f"- baseline_preview: {item.baseline_preview}",
                "",
            ]
        )
        if item.best_candidate is not None:
            lines.extend(
                [
                    (
                        f"- best_candidate: `{item.best_candidate.source}` support="
                        f"`{item.best_candidate.support}` delta=`{item.best_candidate.support_delta}` "
                        f"contract=`{item.best_candidate.contract_score}` "
                        f"novelty=`{item.best_candidate.novelty}` risks=`{item.best_candidate.risks}` "
                        f"contract_flags=`{item.best_candidate.contract_flags}`"
                    ),
                    f"- candidate_preview: {item.best_candidate.answer_preview}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"
