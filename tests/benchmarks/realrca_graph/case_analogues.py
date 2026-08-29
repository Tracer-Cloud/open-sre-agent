from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.bundle_cache import build_evidence_bundle_cached
from tests.benchmarks.realrca_graph.features import clip_text, keyword_features, token_features
from tests.benchmarks.realrca_graph.io import (
    DATASET_DIR,
    DEFAULT_CURRENT_BEST,
    graph_context_path,
    load_cases,
    load_json,
    rows_by_case,
)
from tests.benchmarks.realrca_graph.models import CandidateAnswer, EvidenceBundle
from tests.benchmarks.realrca_graph.probe_feedback import CaseProbeFeedback, ProbeFeedbackLedger
from tests.benchmarks.realrca_graph.validation_memory import (
    DEFAULT_VALIDATION_MEMORY,
    load_validation_memory,
)
from tests.benchmarks.realrca_graph.verifier import score_candidate

MECHANISM_NAMES = {
    "cache",
    "business_metric",
    "change",
    "consume_failure",
    "connection_pool",
    "data_quality",
    "hardware",
    "host",
    "limit",
    "master_data",
    "memory",
    "mq_duplicate_conflict",
    "mq",
    "network",
    "pod",
    "provider_rpc_error",
    "provider_error_qps",
    "repeated_query",
    "security",
    "sql",
    "thread_pool",
    "timeout",
    "traffic_source",
}
MECHANISM_LAYERS: dict[str, tuple[str, ...]] = {
    "cache": ("cache",),
    "business_metric": ("application",),
    "change": ("change",),
    "consume_failure": ("application",),
    "connection_pool": ("database",),
    "data_quality": ("application",),
    "hardware": ("infrastructure",),
    "host": ("infrastructure",),
    "limit": ("middleware_limit",),
    "master_data": ("application",),
    "memory": ("infrastructure",),
    "mq_duplicate_conflict": ("application",),
    "mq": ("message_queue",),
    "network": ("service_dependency",),
    "pod": ("infrastructure",),
    "provider_rpc_error": ("service_dependency",),
    "provider_error_qps": ("service_dependency",),
    "repeated_query": ("database",),
    "security": ("security",),
    "sql": ("database",),
    "thread_pool": ("service_dependency",),
    "timeout": ("service_dependency",),
    "traffic_source": ("database",),
}
KIND_MECHANISMS: dict[str, tuple[str, ...]] = {
    "app_log_limit": ("limit",),
    "business_system_error": ("data_quality",),
    "connection_pool_exhausted": ("connection_pool",),
    "custom_monitor_signal": ("business_metric",),
    "db_access_failure": ("sql",),
    "evidence_sql": ("sql",),
    "external_dependency_failure": ("network", "timeout"),
    "heavy_business_query": ("data_quality",),
    "hsf_threadpool_busy": ("thread_pool", "timeout"),
    "metaq_business_failure": ("consume_failure", "mq"),
    "metaq_duplicate_update_conflict": ("consume_failure", "mq", "mq_duplicate_conflict"),
    "pattern_app_publish_data_quality": ("change", "data_quality"),
    "pattern_cache_timeout": ("cache", "timeout"),
    "pattern_capacity_change": ("change", "host"),
    "pattern_config_mq_failure": ("change", "mq"),
    "pattern_connection_pool": ("connection_pool",),
    "pattern_data_quality": ("data_quality",),
    "pattern_downstream_offline_change": ("change", "timeout"),
    "pattern_external_dependency": ("network", "timeout"),
    "pattern_host_anomaly": ("host",),
    "pattern_hsf_cold_start_capacity": ("change", "host"),
    "pattern_hsf_downstream_timeout": ("timeout",),
    "pattern_hsf_provider_error_qps_spike": ("provider_error_qps",),
    "pattern_hsf_provider_subset_rpc_error": ("provider_rpc_error",),
    "pattern_hsf_threadpool_timeout": ("thread_pool", "timeout"),
    "pattern_instance_count_drop_offline_change": ("change", "host"),
    "pattern_infra_event": ("hardware", "host"),
    "pattern_jvm_memory": ("memory",),
    "pattern_limit": ("limit",),
    "pattern_mdm_master_data_missing": ("master_data",),
    "pattern_metaq_duplicate_update_conflict": (
        "consume_failure",
        "mq",
        "mq_duplicate_conflict",
    ),
    "pattern_mq_spike": ("mq",),
    "pattern_notify_business_failure": ("consume_failure", "mq"),
    "pattern_schedulerx_batch_load": ("data_quality",),
    "pattern_search_dependency": ("network", "timeout"),
    "pattern_security_scan": ("security",),
    "pattern_security_sql_conflict": ("security", "sql"),
    "pattern_slow_sql": ("sql",),
    "pattern_tddl_repeated_query_fanout": ("repeated_query", "sql", "timeout"),
    "pattern_tddl_read_traffic_source": ("traffic_source", "sql"),
    "pattern_threadpool_busy": ("thread_pool",),
    "pod_runtime_event": ("pod",),
    "rds_sql_detail": ("sql",),
    "rds_sql_stat": ("sql",),
    "sql_log_error": ("sql",),
}
ENTITY_PREFIXES = (
    "service:",
    "method:",
    "exception:",
    "rds:",
    "sql_table:",
    "sql_id:",
)
WEAK_ENTITY_PREFIXES = ("app:", "ip:")
NOISY_ENTITY_TOKENS = {
    "app:alibaba-inc",
    "app:app-group",
    "app:aserver-ingress-host",
    "app:aserver-ingress-tao-host",
    "app:center-zb",
    "app:evidence-cluster",
    "app:multi-signal",
    "app:near-alarm",
    "app:provider-service",
    "app:root-cause",
    "app:service-method",
}


@dataclass(frozen=True)
class MechanismProfile:
    """Typed causal mechanism profile for one case or exemplar."""

    case_type: str
    mechanisms: list[str]
    root_layers: list[str]
    modalities: list[str]
    root_kinds: list[str]
    entities: list[str]
    weak_entities: list[str]
    root_labels: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalogueMatch:
    """Public validation exemplar matched to a test case by causal profile."""

    case_id: str
    case_type: str
    similarity: float
    mechanism_score: float
    layer_score: float
    modality_score: float
    entity_score: float
    matched_mechanisms: list[str]
    matched_layers: list[str]
    matched_modalities: list[str]
    matched_entities: list[str]
    root_summary: str
    graph_summary: str
    profile: MechanismProfile

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["profile"] = self.profile.to_dict()
        return payload


@dataclass(frozen=True)
class CaseAnalogue:
    """One test case annotated with public analogue and boundary-diff signals."""

    case_id: str
    case_suffix: str
    case_type: str
    priority: float
    graph_path: str | None
    baseline_support: float
    baseline_risks: list[str]
    top_hypothesis: str
    top_hypothesis_layer: str
    profile: MechanismProfile | None
    matches: list[AnalogueMatch]
    probe_count: int
    best_probe_accuracy: float | None
    probe_agents: list[str]
    categories: list[str]
    recommended_actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_suffix": self.case_suffix,
            "case_type": self.case_type,
            "priority": self.priority,
            "graph_path": self.graph_path,
            "baseline_support": self.baseline_support,
            "baseline_risks": list(self.baseline_risks),
            "top_hypothesis": self.top_hypothesis,
            "top_hypothesis_layer": self.top_hypothesis_layer,
            "profile": self.profile.to_dict() if self.profile is not None else None,
            "matches": [item.to_dict() for item in self.matches],
            "probe_count": self.probe_count,
            "best_probe_accuracy": self.best_probe_accuracy,
            "probe_agents": list(self.probe_agents),
            "categories": list(self.categories),
            "recommended_actions": list(self.recommended_actions),
        }


@dataclass(frozen=True)
class CaseAnalogueReport:
    """Aggregate analogue report used to choose root-boundary experiments."""

    split: str
    baseline_path: str
    graph_roots: list[str]
    validation_memory_path: str
    case_count: int
    best_leaderboard_accuracy: float | None
    category_counts: dict[str, int]
    type_counts: dict[str, int]
    cases: list[CaseAnalogue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "baseline_path": self.baseline_path,
            "graph_roots": list(self.graph_roots),
            "validation_memory_path": self.validation_memory_path,
            "public_validation_truth_used": True,
            "hidden_test_reference_used": False,
            "case_count": self.case_count,
            "best_leaderboard_accuracy": self.best_leaderboard_accuracy,
            "category_counts": dict(self.category_counts),
            "type_counts": dict(self.type_counts),
            "cases": [item.to_dict() for item in self.cases],
        }


def build_case_analogue_report(
    *,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    graph_roots: Sequence[Path] = (),
    split: str = "test",
    validation_memory_path: Path = DEFAULT_VALIDATION_MEMORY,
    dataset_dir: Path = DATASET_DIR,
    case_ids: Sequence[str] = (),
    match_limit: int = 3,
    leaderboard_path: Path | None = None,
    team_name: str = "隐元玩一玩",
) -> CaseAnalogueReport:
    """Match cases to public validation mechanism analogues without hidden labels."""

    roots = list(graph_roots)
    memory = load_validation_memory(validation_memory_path) or {}
    exemplars = _memory_profiles(memory)
    feedback_ledger = _feedback_ledger(leaderboard_path, team_name)
    baseline_rows = rows_by_case(baseline_path, source=baseline_path.stem)
    case_meta = _case_meta(split, dataset_dir)
    selected = set(case_ids)
    cases: list[CaseAnalogue] = []
    for case_id, baseline in baseline_rows.items():
        if selected and case_id not in selected:
            continue
        graph_path = _find_graph_context_path(roots, split, case_id)
        case_type = _case_type(case_id, case_meta)
        if graph_path is None:
            probe_feedback = (
                feedback_ledger.for_case_id(case_id) if feedback_ledger is not None else None
            )
            categories = ["missing_graph_context"]
            if _known_negative_probe(probe_feedback, feedback_ledger):
                categories.append("known_negative_probe")
            cases.append(
                CaseAnalogue(
                    case_id=case_id,
                    case_suffix=_case_suffix(case_id),
                    case_type=case_type,
                    priority=_priority(None, [], categories, []),
                    graph_path=None,
                    baseline_support=0.0,
                    baseline_risks=[],
                    top_hypothesis="",
                    top_hypothesis_layer="",
                    profile=None,
                    matches=[],
                    probe_count=len(probe_feedback.records) if probe_feedback is not None else 0,
                    best_probe_accuracy=_best_probe_accuracy(probe_feedback),
                    probe_agents=_probe_agents(probe_feedback),
                    categories=categories,
                    recommended_actions=_recommended_actions(categories),
                )
            )
            continue
        bundle = build_evidence_bundle_cached(graph_path)
        case_type = bundle.case_type or case_type
        profile = profile_from_bundle(bundle, answer=baseline)
        matches = _match_profiles(profile, exemplars, limit=match_limit)
        baseline_score = score_candidate(baseline, baseline, bundle)
        probe_feedback = (
            feedback_ledger.for_case_id(case_id) if feedback_ledger is not None else None
        )
        top = bundle.hypotheses[0] if bundle.hypotheses else None
        categories = _categories(
            profile=profile,
            matches=matches,
            baseline=baseline,
            baseline_risks=baseline_score.risk_flags,
            top_layer=top.root_layer if top is not None else "",
            probe_feedback=probe_feedback,
            feedback_ledger=feedback_ledger,
        )
        cases.append(
            CaseAnalogue(
                case_id=case_id,
                case_suffix=_case_suffix(case_id),
                case_type=case_type,
                priority=_priority(profile, matches, categories, baseline_score.risk_flags),
                graph_path=str(graph_path),
                baseline_support=baseline_score.graph_support,
                baseline_risks=list(baseline_score.risk_flags),
                top_hypothesis=clip_text(top.label, 180) if top is not None else "",
                top_hypothesis_layer=top.root_layer if top is not None else "",
                profile=profile,
                matches=matches,
                probe_count=len(probe_feedback.records) if probe_feedback is not None else 0,
                best_probe_accuracy=_best_probe_accuracy(probe_feedback),
                probe_agents=_probe_agents(probe_feedback),
                categories=categories,
                recommended_actions=_recommended_actions(categories),
            )
        )
    cases.sort(key=lambda item: (-item.priority, item.case_type, item.case_id))
    return CaseAnalogueReport(
        split=split,
        baseline_path=str(baseline_path),
        graph_roots=[str(root) for root in roots],
        validation_memory_path=str(validation_memory_path),
        case_count=len(cases),
        best_leaderboard_accuracy=(
            feedback_ledger.reference_accuracy if feedback_ledger is not None else None
        ),
        category_counts=dict(Counter(category for item in cases for category in item.categories)),
        type_counts=dict(Counter(item.case_type for item in cases)),
        cases=cases,
    )


def profile_from_bundle(
    bundle: EvidenceBundle, *, answer: CandidateAnswer | None = None
) -> MechanismProfile:
    """Build a mechanism profile from an evidence bundle and optional baseline answer."""

    roots = bundle.hypotheses[:4]
    root_payload = [
        {
            "kind": item.kind,
            "label": item.label,
            "root_layer": item.root_layer,
            "reason": item.reason,
            "entities": item.entities,
        }
        for item in roots
    ]
    support_items = [support for root in roots for support in root.support]
    evidence_payload = [
        {
            "name": item.name,
            "modality": item.modality,
            "summary": item.summary,
        }
        for item in support_items[:16]
    ]
    root_tokens = token_features(
        {
            "case_type": bundle.case_type,
            "roots": root_payload,
            "evidence": evidence_payload,
        }
    )
    answer_tokens = token_features(answer.diagnosis_output) if answer is not None else set()
    tokens = set(root_tokens)
    if answer is not None:
        tokens.update(_entity_tokens(answer_tokens, weak=False))
        tokens.update(_entity_tokens(answer_tokens, weak=True))
    mechanisms = _mechanisms_from_tokens(root_tokens)
    root_layers = {item.root_layer for item in roots if item.root_layer}
    if not root_layers:
        root_layers.update(_layers_from_mechanisms(mechanisms))
    modalities = {
        item.modality for item in support_items if item.modality and item.modality != "other"
    }
    modalities.update(
        item.modality for item in bundle.evidence[:40] if item.modality and item.modality != "other"
    )
    for item in roots:
        modalities.update(name for name in item.modalities if name and name != "other")
    return MechanismProfile(
        case_type=bundle.case_type,
        mechanisms=sorted(mechanisms),
        root_layers=sorted(root_layers),
        modalities=sorted(modalities),
        root_kinds=sorted({item.kind for item in roots if item.kind}),
        entities=sorted(_entity_tokens(tokens, weak=False)),
        weak_entities=sorted(_entity_tokens(tokens, weak=True)),
        root_labels=[clip_text(item.label, 160) for item in roots[:4]],
    )


def render_case_analogue_markdown(report: CaseAnalogueReport, *, limit: int = 50) -> str:
    """Render a compact analogue report for root-boundary review."""

    lines = [
        "# RealRCA Case Analogues",
        "",
        f"- split: `{report.split}`",
        f"- cases: `{report.case_count}`",
        f"- baseline: `{report.baseline_path}`",
        f"- graph_roots: `{report.graph_roots}`",
        f"- validation_memory: `{report.validation_memory_path}`",
        f"- best_leaderboard_accuracy: `{report.best_leaderboard_accuracy}`",
        "- public_validation_truth_used: `True`",
        "- hidden_test_reference_used: `False`",
        f"- top_categories: `{_top_counts(report.category_counts)}`",
        "",
        "## Priority Cases",
        "",
        "| rank | case | type | priority | baseline_support | probes | top_root | analogue | similarity | categories | action |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        match = item.matches[0] if item.matches else None
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type,
                    f"{item.priority:.3f}",
                    f"{item.baseline_support:.4f}",
                    str(item.probe_count),
                    _markdown_cell(item.top_hypothesis or "-"),
                    f"`{match.case_id[-4:]}`" if match is not None else "-",
                    f"{match.similarity:.4f}" if match is not None else "-",
                    ",".join(item.categories[:4]) or "-",
                    _markdown_cell(
                        item.recommended_actions[0] if item.recommended_actions else "-"
                    ),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Category Counts", ""])
    for category, count in sorted(
        report.category_counts.items(), key=lambda entry: (-entry[1], entry[0])
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
                f"- baseline_support: `{item.baseline_support:.4f}`; risks: `{item.baseline_risks}`",
                (
                    f"- probes: count=`{item.probe_count}` best_accuracy="
                    f"`{item.best_probe_accuracy}` agents=`{item.probe_agents}`"
                ),
                f"- top_hypothesis: {item.top_hypothesis or '-'}",
                f"- top_layer: `{item.top_hypothesis_layer or '-'}`",
                f"- profile: `{item.profile.to_dict() if item.profile is not None else None}`",
                f"- categories: `{item.categories}`",
                f"- recommended_actions: `{item.recommended_actions}`",
                "",
            ]
        )
        for match in item.matches:
            lines.extend(
                [
                    (
                        f"- analogue `{match.case_id}` type=`{match.case_type}` "
                        f"similarity=`{match.similarity}` mechanism=`{match.mechanism_score}` "
                        f"layer=`{match.layer_score}` modality=`{match.modality_score}` "
                        f"entity=`{match.entity_score}`"
                    ),
                    (
                        f"  matched_mechanisms=`{match.matched_mechanisms}` "
                        f"matched_layers=`{match.matched_layers}` "
                        f"matched_modalities=`{match.matched_modalities}` "
                        f"matched_entities=`{match.matched_entities}`"
                    ),
                    f"  root_summary: {match.root_summary or '-'}",
                    f"  graph_summary: {match.graph_summary or '-'}",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _memory_profiles(memory: dict[str, Any]) -> list[tuple[dict[str, Any], MechanismProfile]]:
    entries = memory.get("entries")
    if not isinstance(entries, list):
        return []
    output: list[tuple[dict[str, Any], MechanismProfile]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        output.append((entry, _profile_from_memory_entry(entry)))
    return output


def _profile_from_memory_entry(entry: dict[str, Any]) -> MechanismProfile:
    raw_tokens = entry.get("feature_tokens")
    feature_tokens = (
        {str(token) for token in raw_tokens if isinstance(token, str)}
        if isinstance(raw_tokens, list)
        else set()
    )
    truth = entry.get("truth") if isinstance(entry.get("truth"), dict) else {}
    graph = entry.get("graph") if isinstance(entry.get("graph"), dict) else {}
    truth_tokens = token_features(truth)
    graph_focus_tokens = token_features(graph.get("top_root_candidates") or [])
    kind_tokens = {token for token in feature_tokens if token.startswith("kind:")}
    entity_feature_tokens = _entity_tokens(feature_tokens, weak=False) | _entity_tokens(
        feature_tokens,
        weak=True,
    )
    tokens = truth_tokens | graph_focus_tokens | kind_tokens | entity_feature_tokens
    case_type = str(entry.get("case_type") or "")
    mechanisms = _mechanisms_from_tokens(truth_tokens | graph_focus_tokens | kind_tokens)
    root_layers = _layers_from_mechanisms(mechanisms)
    root_kinds = {token.removeprefix("kind:") for token in kind_tokens if token.startswith("kind:")}
    modalities = _modalities_from_tokens(truth_tokens | graph_focus_tokens | kind_tokens)
    root_labels = _validation_root_labels(truth, graph)
    return MechanismProfile(
        case_type=case_type,
        mechanisms=sorted(mechanisms),
        root_layers=sorted(root_layers),
        modalities=sorted(modalities),
        root_kinds=sorted(root_kinds),
        entities=sorted(_entity_tokens(tokens, weak=False)),
        weak_entities=sorted(_entity_tokens(tokens, weak=True)),
        root_labels=root_labels,
    )


def _match_profiles(
    query: MechanismProfile,
    exemplars: Sequence[tuple[dict[str, Any], MechanismProfile]],
    *,
    limit: int,
) -> list[AnalogueMatch]:
    matches: list[AnalogueMatch] = []
    for entry, exemplar in exemplars:
        scores = _profile_similarity(query, exemplar)
        if scores["similarity"] <= 0:
            continue
        truth = entry.get("truth") if isinstance(entry.get("truth"), dict) else {}
        graph = entry.get("graph") if isinstance(entry.get("graph"), dict) else {}
        matches.append(
            AnalogueMatch(
                case_id=str(entry.get("case_id") or ""),
                case_type=exemplar.case_type,
                similarity=scores["similarity"],
                mechanism_score=scores["mechanism"],
                layer_score=scores["layer"],
                modality_score=scores["modality"],
                entity_score=scores["entity"],
                matched_mechanisms=_sorted_overlap(query.mechanisms, exemplar.mechanisms),
                matched_layers=_sorted_overlap(query.root_layers, exemplar.root_layers),
                matched_modalities=_sorted_overlap(query.modalities, exemplar.modalities),
                matched_entities=_sorted_overlap(
                    list(query.entities) + list(query.weak_entities),
                    list(exemplar.entities) + list(exemplar.weak_entities),
                )[:12],
                root_summary=_truth_root_summary(truth),
                graph_summary=clip_text(str(graph.get("retrieval_summary") or ""), 420),
                profile=exemplar,
            )
        )
    matches.sort(
        key=lambda item: (
            -item.similarity,
            -item.mechanism_score,
            -item.layer_score,
            -item.entity_score,
            item.case_id,
        )
    )
    return matches[:limit]


def _profile_similarity(query: MechanismProfile, exemplar: MechanismProfile) -> dict[str, float]:
    mechanism = _coverage(query.mechanisms, exemplar.mechanisms)
    layer = _coverage(query.root_layers, exemplar.root_layers)
    modality = _coverage(query.modalities, exemplar.modalities)
    entity = max(
        _capped_overlap(query.entities, exemplar.entities, cap=5),
        0.6 * _capped_overlap(query.weak_entities, exemplar.weak_entities, cap=4),
    )
    kind = 0.25 * _coverage(query.root_kinds, exemplar.root_kinds)
    type_bonus = 0.08 if query.case_type and query.case_type == exemplar.case_type else 0.0
    similarity = min(
        1.0,
        0.46 * mechanism + 0.24 * layer + 0.14 * modality + 0.12 * entity + kind + type_bonus,
    )
    return {
        "similarity": round(similarity, 4),
        "mechanism": round(mechanism, 4),
        "layer": round(layer, 4),
        "modality": round(modality, 4),
        "entity": round(entity, 4),
    }


def _categories(
    *,
    profile: MechanismProfile,
    matches: Sequence[AnalogueMatch],
    baseline: CandidateAnswer,
    baseline_risks: Sequence[str],
    top_layer: str,
    probe_feedback: CaseProbeFeedback | None,
    feedback_ledger: ProbeFeedbackLedger | None,
) -> list[str]:
    categories: list[str] = []
    if not matches:
        categories.append("no_public_validation_analogue")
    else:
        best = matches[0]
        if best.similarity < 0.42:
            categories.append("low_similarity_public_analogue")
        if best.profile.root_layers and top_layer and top_layer not in best.profile.root_layers:
            categories.append(f"top_layer_diff:{top_layer}->{','.join(best.profile.root_layers)}")
        baseline_layers = _layers_from_mechanisms(
            _mechanisms_from_tokens(token_features(baseline.diagnosis_output))
        )
        if (
            baseline_layers
            and best.profile.root_layers
            and not baseline_layers & set(best.profile.root_layers)
        ):
            categories.append(
                "baseline_layer_diff:"
                + ",".join(sorted(baseline_layers))
                + "->"
                + ",".join(best.profile.root_layers)
            )
        if not set(profile.mechanisms) & set(best.profile.mechanisms):
            categories.append("analogue_mechanism_gap")
        if _ambiguous_top_matches(matches):
            categories.append("ambiguous_public_analogues")
    if len(profile.modalities) < 2:
        categories.append("single_modality_profile")
    for risk in baseline_risks:
        categories.append(f"verifier_risk:{risk}")
    if _known_negative_probe(probe_feedback, feedback_ledger):
        categories.append("known_negative_probe")
    return _unique(categories)


def _recommended_actions(categories: Sequence[str]) -> list[str]:
    category_set = set(categories)
    actions: list[str] = []
    if "missing_graph_context" in category_set:
        actions.append("先补 graph_context，再做 analogue/root-boundary 判断。")
    if (
        "no_public_validation_analogue" in category_set
        or "low_similarity_public_analogue" in category_set
    ):
        actions.append("优先补新证据源或扩展公开 validation 机制记忆，不要直接生成答案。")
    if any(item.startswith("top_layer_diff:") for item in categories):
        actions.append("做人审 root-boundary：比较当前 top root 与公开相似机制的根因层级。")
    if any(item.startswith("baseline_layer_diff:") for item in categories):
        actions.append("构造保留 baseline 实体的最小对照候选，再用 verifier 和单 case 榜单 probe。")
    if "ambiguous_public_analogues" in category_set:
        actions.append("把相邻机制拆成 counterfactual evidence bundle，避免一次性大改。")
    if "single_modality_profile" in category_set:
        actions.append("补第二模态证据后再让 DMA 生成候选。")
    if any(item.startswith("verifier_risk:") for item in categories):
        actions.append("先修 verifier 风险或改成证据采集任务，不直接提交。")
    if "known_negative_probe" in category_set:
        actions.append("该 case 已有负反馈；除非新增证据源，不再重复同类改写。")
    if not actions:
        actions.append("保持当前 best；只有新增证据改变根因边界时再 probe。")
    return _unique(actions)


def _priority(
    profile: MechanismProfile | None,
    matches: Sequence[AnalogueMatch],
    categories: Sequence[str],
    baseline_risks: Sequence[str],
) -> float:
    score = 0.0
    if matches:
        score += matches[0].similarity * 8.0
    if profile is not None and len(profile.modalities) < 2:
        score += 3.0
    if any(category.startswith("top_layer_diff:") for category in categories):
        score += 7.0
    if any(category.startswith("baseline_layer_diff:") for category in categories):
        score += 5.0
    if "ambiguous_public_analogues" in categories:
        score += 4.0
    if "low_similarity_public_analogue" in categories:
        score += 2.0
    if "no_public_validation_analogue" in categories:
        score += 3.0
    score += min(6.0, 1.5 * len(baseline_risks))
    if "known_negative_probe" in categories:
        score -= 6.0
    return round(score, 3)


def _feedback_ledger(leaderboard_path: Path | None, team_name: str) -> ProbeFeedbackLedger | None:
    if leaderboard_path is None or not leaderboard_path.exists():
        return None
    payload = load_json(leaderboard_path)
    if not isinstance(payload, dict):
        return None
    return ProbeFeedbackLedger.from_leaderboard(payload, team_name=team_name)


def _known_negative_probe(
    feedback: CaseProbeFeedback | None,
    ledger: ProbeFeedbackLedger | None,
) -> bool:
    return (
        feedback is not None
        and ledger is not None
        and feedback.negative_count > 0
        and (feedback.best_delta is None or feedback.best_delta <= 0)
    )


def _best_probe_accuracy(feedback: CaseProbeFeedback | None) -> float | None:
    if feedback is None or not feedback.records:
        return None
    return max(record.accuracy for record in feedback.records)


def _probe_agents(feedback: CaseProbeFeedback | None) -> list[str]:
    if feedback is None:
        return []
    return [record.agent_name for record in feedback.records[:5]]


def _mechanisms_from_tokens(tokens: set[str]) -> set[str]:
    mechanisms = {
        token.removeprefix("keyword:")
        for token in tokens
        if token.startswith("keyword:") and token.removeprefix("keyword:") in MECHANISM_NAMES
    }
    mechanisms.update(
        name for name in keyword_features(" ".join(tokens)) if name in MECHANISM_NAMES
    )
    for token in tokens:
        if not token.startswith("kind:"):
            continue
        mechanisms.update(KIND_MECHANISMS.get(token.removeprefix("kind:"), ()))
    return mechanisms


def _layers_from_mechanisms(mechanisms: set[str]) -> set[str]:
    layers: set[str] = set()
    for mechanism in mechanisms:
        layers.update(MECHANISM_LAYERS.get(mechanism, ()))
    return layers


def _entity_tokens(tokens: set[str], *, weak: bool) -> set[str]:
    prefixes = WEAK_ENTITY_PREFIXES if weak else ENTITY_PREFIXES
    return {
        token
        for token in tokens
        if token.startswith(prefixes)
        and token not in NOISY_ENTITY_TOKENS
        and not token.startswith("app:aserver")
    }


def _modalities_from_tokens(tokens: set[str]) -> set[str]:
    modalities: set[str] = set()
    for token in tokens:
        lower = token.lower()
        if token.startswith(("metric:", "node_metric:")) or "metric" in lower:
            modalities.add("metric")
        if (
            token.startswith(("rds:", "sql_", "node_sql:", "node_rds:"))
            or "tddl" in lower
            or "sql" in lower
        ):
            modalities.add("sql")
        if "trace" in lower or token.startswith("kind:hsf_"):
            modalities.add("trace")
        if "event" in lower or "change" in lower or "deploy" in lower:
            modalities.add("event")
        if token.startswith("exception:") or "sls" in lower or "log" in lower:
            modalities.add("log")
    return modalities


def _coverage(query_values: Sequence[str], exemplar_values: Sequence[str]) -> float:
    query = set(query_values)
    exemplar = set(exemplar_values)
    if not query or not exemplar:
        return 0.0
    return len(query & exemplar) / len(query)


def _capped_overlap(
    query_values: Sequence[str], exemplar_values: Sequence[str], *, cap: int
) -> float:
    query = set(query_values)
    exemplar = set(exemplar_values)
    if not query or not exemplar:
        return 0.0
    return min(1.0, len(query & exemplar) / min(cap, len(query)))


def _ambiguous_top_matches(matches: Sequence[AnalogueMatch]) -> bool:
    if len(matches) < 2:
        return False
    first, second = matches[0], matches[1]
    if first.similarity - second.similarity > 0.05:
        return False
    return not set(first.profile.mechanisms) & set(second.profile.mechanisms)


def _validation_root_labels(truth: dict[str, Any], graph: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    chain = truth.get("root_cause_chain") if isinstance(truth.get("root_cause_chain"), list) else []
    for item in chain[:4]:
        if not isinstance(item, dict):
            continue
        component = item.get("component") if isinstance(item.get("component"), dict) else {}
        labels.append(
            clip_text(
                " ".join(
                    part
                    for part in (
                        str(item.get("description") or ""),
                        str(component.get("name") or ""),
                        str(component.get("type") or ""),
                    )
                    if part
                ),
                160,
            )
        )
    for item in (
        graph.get("top_root_candidates", [])[:4]
        if isinstance(graph.get("top_root_candidates"), list)
        else []
    ):
        if isinstance(item, dict):
            labels.append(clip_text(str(item.get("label") or ""), 160))
    return [item for item in labels if item]


def _truth_root_summary(truth: dict[str, Any]) -> str:
    chain = truth.get("root_cause_chain") if isinstance(truth.get("root_cause_chain"), list) else []
    parts: list[str] = []
    for item in chain[:4]:
        if not isinstance(item, dict):
            continue
        component = item.get("component") if isinstance(item.get("component"), dict) else {}
        parts.append(
            " ".join(
                part
                for part in (
                    str(item.get("type") or ""),
                    str(item.get("description") or ""),
                    f"component={component.get('name')}/{component.get('type')}"
                    if component
                    else "",
                )
                if part
            )
        )
    return clip_text(" | ".join(parts), 520)


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


def _case_type(case_id: str, case_meta: dict[str, dict[str, Any]]) -> str:
    row = case_meta.get(case_id) or {}
    return str(row.get("type") or row.get("case_type") or "unknown")


def _find_graph_context_path(graph_roots: Sequence[Path], split: str, case_id: str) -> Path | None:
    for root in graph_roots:
        path = graph_context_path(root, split, case_id)
        if path.exists():
            return path
    return None


def _case_suffix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[-1][-4:]


def _sorted_overlap(left: Sequence[str], right: Sequence[str]) -> list[str]:
    return sorted(set(left) & set(right))


def _unique(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _top_counts(counts: dict[str, int], *, limit: int = 8) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
