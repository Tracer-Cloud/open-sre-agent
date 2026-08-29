from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.features import clip_text, keyword_features, token_features
from tests.benchmarks.realrca_graph.io import (
    DATASET_DIR,
    DEFAULT_CURRENT_BEST,
    load_json,
    rows_by_case,
)
from tests.benchmarks.realrca_graph.validation import _required_items, _truth_rows

ITEM_COVERAGE_THRESHOLD = 0.18
PARTIAL_ITEM_THRESHOLD = 0.04
HARD_SCORE_BLOCKERS = {
    "case_negative_probe_history",
    "current_best_probe_anchor",
    "do_not_submit_trace_only",
    "known_negative_probe",
    "large_negative_probe_delta",
    "negative_tomography_variant",
    "top_hypothesis_negated_by_baseline",
}
PUBLIC_ENTITY_PREFIXES = ("app:", "service:", "method:", "sql_table:", "rds:", "ip:")
SAFE_HINTS = {
    "auth": "明确认证/登录态/权限失败如何触发本案告警，保留本案自己的应用和接口名。",
    "business_metric": "明确业务监控指标的失败数或成功率异常，以及它和根因的因果关系。",
    "cache": "明确缓存命中率、热点 key 或 Redis/Tair 超时是否只是放大器，保留本案缓存实例。",
    "change": "只有本案存在变更证据时，才补充发布/缩容/重启与告警窗口的时序关系。",
    "connection_pool": "明确数据库连接池耗尽或连接获取失败，避免改写成普通慢 SQL。",
    "consume_failure": "明确 MQ 消费失败发生在业务处理阶段，并写出导致重试/成功率下跌的链路。",
    "data_quality": "明确业务参数、主数据、字符集、唯一键或余额等数据契约异常。",
    "hardware": "明确宿主机、ECS 或硬件事件是根因而非应用侧伴随症状。",
    "host": "明确单机/分组异常和路由命中关系，避免泛化成集群整体故障。",
    "limit": "明确 Sentinel/限流/请求被拒绝是根因机制，而非普通超时。",
    "memory": "只有本案有 JVM/GC 证据时，才补充 Full GC、STW 或内存耗尽表述。",
    "mq": "明确 broker、topic/group、消费/拉取/连接失败的具体主链路。",
    "mq_duplicate_conflict": "明确重复消息、幂等或乐观锁冲突如何导致消费失败。",
    "network": "明确连接拒绝、重置、不可达或 DNS/网络异常发生在哪个依赖边界。",
    "pod": "明确 pod eviction/OOMKilled/容器事件和服务失败的时序关系。",
    "provider_error_qps": "明确 provider 侧错误 QPS 或成功率下跌的接口边界。",
    "provider_rpc_error": "明确 provider 子集 RPC_ERROR，不要凭空推断序列化或 Hessian 细节。",
    "repeated_query": "明确重复 SQL fanout 或 N+1 查询如何拉长接口 RT。",
    "security": "明确安全扫描/恶意 payload 触发参数校验或异常，而不是泛化成流量突增。",
    "sql": "明确 SQL 表、SQL_ID、慢查询或写入异常，保留本案实际数据库实体。",
    "thread_pool": "只有本案有线程池直接证据时，才补充 HSF 线程池打满/队列满。",
    "timeout": "明确下游接口超时/失败发生在哪条调用边上，并说明它如何传播到入口告警。",
    "traffic_source": "明确读流量来源或上游应用，而不是把流量症状写成数据库自身故障。",
}


@dataclass(frozen=True)
class ContractGapItem:
    """One public-analogue contract item evaluated against a hidden/test answer."""

    case_id: str
    case_suffix: str
    case_type: str
    analogue_case_id: str
    analogue_suffix: str
    analogue_similarity: float
    analogue_item_name: str
    analogue_item_description: str
    baseline_item_score: float
    item_mechanisms: list[str]
    matched_mechanisms: list[str]
    aligned_mechanisms: list[str]
    public_entity_tokens: list[str]
    shared_entity_tokens: list[str]
    foreign_entity_tokens: list[str]
    score_boundary_action: str
    score_boundary_blockers: list[str]
    category: str
    action: str
    safe_hint: str
    baseline_preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContractGapCase:
    """Contract-gap summary for one hidden/test case."""

    case_id: str
    case_suffix: str
    case_type: str
    profile_mechanisms: list[str]
    score_boundary_action: str
    score_boundary_blockers: list[str]
    gap_count: int
    category_counts: dict[str, int]
    recommended_action: str
    items: list[ContractGapItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_suffix": self.case_suffix,
            "case_type": self.case_type,
            "profile_mechanisms": list(self.profile_mechanisms),
            "score_boundary_action": self.score_boundary_action,
            "score_boundary_blockers": list(self.score_boundary_blockers),
            "gap_count": self.gap_count,
            "category_counts": dict(self.category_counts),
            "recommended_action": self.recommended_action,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class ContractGapReport:
    """Public-validation analogue contract gaps for hidden-safe experiment planning."""

    analogue_path: str
    baseline_path: str
    score_boundary_path: str
    case_count: int
    item_count: int
    category_counts: dict[str, int]
    action_counts: dict[str, int]
    cases: list[ContractGapCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "analogue_path": self.analogue_path,
            "baseline_path": self.baseline_path,
            "score_boundary_path": self.score_boundary_path,
            "public_validation_truth_used": True,
            "hidden_test_reference_used": False,
            "case_count": self.case_count,
            "item_count": self.item_count,
            "category_counts": dict(self.category_counts),
            "action_counts": dict(self.action_counts),
            "cases": [item.to_dict() for item in self.cases],
        }


def build_contract_gap_report(
    *,
    analogue_path: Path,
    baseline_path: Path = DEFAULT_CURRENT_BEST,
    score_boundary_path: Path | None = None,
    dataset_dir: Path = DATASET_DIR,
    match_limit: int = 3,
    min_similarity: float = 0.78,
    item_coverage_threshold: float = ITEM_COVERAGE_THRESHOLD,
) -> ContractGapReport:
    """Compare public validation analogue contracts with hidden/test baseline answers."""

    analogue_payload = load_json(analogue_path)
    baseline = rows_by_case(baseline_path, source=baseline_path.stem)
    truths = _truth_rows(dataset_dir)
    boundaries = _score_boundaries(score_boundary_path)
    cases: list[ContractGapCase] = []

    for raw_case in _list_dicts(analogue_payload.get("cases")):
        case_id = str(raw_case.get("case_id") or "")
        baseline_answer = baseline.get(case_id)
        if not case_id or baseline_answer is None:
            continue
        profile = raw_case.get("profile") if isinstance(raw_case.get("profile"), dict) else {}
        profile_mechanisms = _str_list(profile.get("mechanisms"))
        boundary = boundaries.get(case_id, {})
        case_items = _case_gap_items(
            raw_case=raw_case,
            baseline_text=baseline_answer.diagnosis_output,
            baseline_case_type=str(raw_case.get("case_type") or ""),
            truths=truths,
            profile_mechanisms=profile_mechanisms,
            boundary=boundary,
            match_limit=match_limit,
            min_similarity=min_similarity,
            item_coverage_threshold=item_coverage_threshold,
        )
        case_items.sort(
            key=lambda item: (
                _item_priority(item),
                -item.analogue_similarity,
                item.analogue_suffix,
                item.analogue_item_name,
            )
        )
        category_counts = Counter(item.category for item in case_items)
        recommended_action = _case_recommended_action(case_items, boundary)
        cases.append(
            ContractGapCase(
                case_id=case_id,
                case_suffix=_case_suffix(case_id),
                case_type=str(raw_case.get("case_type") or ""),
                profile_mechanisms=profile_mechanisms,
                score_boundary_action=str(boundary.get("action") or ""),
                score_boundary_blockers=_str_list(boundary.get("blockers")),
                gap_count=sum(1 for item in case_items if item.category != "already_covered"),
                category_counts=dict(sorted(category_counts.items())),
                recommended_action=recommended_action,
                items=case_items,
            )
        )

    cases.sort(
        key=lambda item: (
            _case_priority(item),
            -item.gap_count,
            item.case_type,
            item.case_suffix,
        )
    )
    all_items = [item for case in cases for item in case.items]
    return ContractGapReport(
        analogue_path=str(analogue_path),
        baseline_path=str(baseline_path),
        score_boundary_path=str(score_boundary_path or ""),
        case_count=len(cases),
        item_count=len(all_items),
        category_counts=dict(sorted(Counter(item.category for item in all_items).items())),
        action_counts=dict(sorted(Counter(item.action for item in all_items).items())),
        cases=cases,
    )


def render_contract_gap_markdown(report: ContractGapReport, *, limit: int = 60) -> str:
    """Render a compact contract-gap report for experiment planning."""

    lines = [
        "# RealRCA Analogue Contract Gaps",
        "",
        f"- analogue: `{report.analogue_path}`",
        f"- baseline: `{report.baseline_path}`",
        f"- score_boundary: `{report.score_boundary_path}`",
        "- public_validation_truth_used: `True`",
        "- hidden_test_reference_used: `False`",
        f"- cases: `{report.case_count}`",
        f"- items: `{report.item_count}`",
        f"- category_counts: `{_top_counts(report.category_counts)}`",
        f"- action_counts: `{_top_counts(report.action_counts)}`",
        "",
        "| rank | case | type | gaps | recommended | categories | blockers |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type or "-",
                    str(item.gap_count),
                    item.recommended_action,
                    _top_counts(item.category_counts, limit=4) or "-",
                    ",".join(item.score_boundary_blockers[:3]) or "-",
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
                f"- profile_mechanisms: `{item.profile_mechanisms}`",
                (
                    f"- score_boundary: action=`{item.score_boundary_action}` "
                    f"blockers=`{item.score_boundary_blockers}`"
                ),
                f"- recommended_action: `{item.recommended_action}`",
                "",
            ]
        )
        for gap in item.items[:8]:
            lines.extend(
                [
                    (
                        f"- `{gap.category}` via validation analogue `{gap.analogue_suffix}` "
                        f"similarity=`{gap.analogue_similarity}` item=`{gap.analogue_item_name}` "
                        f"baseline_item_score=`{gap.baseline_item_score}`"
                    ),
                    f"  aligned_mechanisms=`{gap.aligned_mechanisms}` matched=`{gap.matched_mechanisms}`",
                    f"  public_entities=`{gap.public_entity_tokens}` foreign=`{gap.foreign_entity_tokens}`",
                    f"  action=`{gap.action}` safe_hint=`{gap.safe_hint}`",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _case_gap_items(
    *,
    raw_case: dict[str, Any],
    baseline_text: str,
    baseline_case_type: str,
    truths: dict[str, dict[str, Any]],
    profile_mechanisms: list[str],
    boundary: dict[str, Any],
    match_limit: int,
    min_similarity: float,
    item_coverage_threshold: float,
) -> list[ContractGapItem]:
    case_id = str(raw_case.get("case_id") or "")
    profile_tokens = token_features(
        {
            "profile": raw_case.get("profile"),
            "baseline": baseline_text,
        }
    )
    baseline_tokens = token_features(baseline_text)
    profile_mechanism_set = set(profile_mechanisms)
    items: list[ContractGapItem] = []
    for match in _list_dicts(raw_case.get("matches"))[:match_limit]:
        similarity = _float(match.get("similarity"))
        if similarity < min_similarity:
            continue
        if str(match.get("split") or "") != "validation":
            continue
        matched_mechanisms = _str_list(match.get("matched_mechanisms"))
        if not matched_mechanisms:
            continue
        truth = truths.get(str(match.get("case_id") or ""))
        if truth is None:
            continue
        for required in _required_items(truth):
            if not required.get("critical"):
                continue
            items.append(
                _contract_gap_item(
                    case_id=case_id,
                    case_type=baseline_case_type,
                    match=match,
                    required=required,
                    baseline_tokens=baseline_tokens,
                    profile_tokens=profile_tokens,
                    profile_mechanisms=profile_mechanism_set,
                    matched_mechanisms=set(matched_mechanisms),
                    boundary=boundary,
                    item_coverage_threshold=item_coverage_threshold,
                    baseline_preview=clip_text(baseline_text, 220),
                )
            )
    return items


def _contract_gap_item(
    *,
    case_id: str,
    case_type: str,
    match: dict[str, Any],
    required: dict[str, Any],
    baseline_tokens: set[str],
    profile_tokens: set[str],
    profile_mechanisms: set[str],
    matched_mechanisms: set[str],
    boundary: dict[str, Any],
    item_coverage_threshold: float,
    baseline_preview: str,
) -> ContractGapItem:
    item_tokens = token_features(required)
    public_entities = _public_entity_tokens(item_tokens)
    shared_entities = sorted(public_entities & profile_tokens)
    foreign_entities = sorted(public_entities - profile_tokens)
    item_mechanisms = keyword_features(json.dumps(required, ensure_ascii=False))
    aligned_mechanisms = sorted(item_mechanisms & matched_mechanisms & profile_mechanisms)
    baseline_item_score = _score_item(baseline_tokens, item_tokens)
    boundary_blockers = _str_list(boundary.get("blockers"))
    category = _category(
        aligned_mechanisms=aligned_mechanisms,
        baseline_item_score=baseline_item_score,
        foreign_entities=foreign_entities,
        boundary_blockers=boundary_blockers,
        threshold=item_coverage_threshold,
    )
    action = _item_action(category)
    return ContractGapItem(
        case_id=case_id,
        case_suffix=_case_suffix(case_id),
        case_type=case_type,
        analogue_case_id=str(match.get("case_id") or ""),
        analogue_suffix=_case_suffix(str(match.get("case_id") or "")),
        analogue_similarity=_float(match.get("similarity")),
        analogue_item_name=str(required.get("name") or ""),
        analogue_item_description=clip_text(required.get("description") or "", 220),
        baseline_item_score=round(baseline_item_score, 4),
        item_mechanisms=sorted(item_mechanisms),
        matched_mechanisms=sorted(matched_mechanisms),
        aligned_mechanisms=aligned_mechanisms,
        public_entity_tokens=sorted(public_entities),
        shared_entity_tokens=shared_entities,
        foreign_entity_tokens=foreign_entities,
        score_boundary_action=str(boundary.get("action") or ""),
        score_boundary_blockers=boundary_blockers,
        category=category,
        action=action,
        safe_hint=_safe_hint(aligned_mechanisms, category),
        baseline_preview=baseline_preview,
    )


def _category(
    *,
    aligned_mechanisms: list[str],
    baseline_item_score: float,
    foreign_entities: list[str],
    boundary_blockers: list[str],
    threshold: float,
) -> str:
    if baseline_item_score >= threshold:
        return "already_covered"
    if not aligned_mechanisms:
        return "mechanism_noise"
    if foreign_entities:
        return "foreign_public_entity_noise"
    if set(boundary_blockers) & HARD_SCORE_BLOCKERS:
        return "blocked_by_score_feedback"
    if baseline_item_score >= PARTIAL_ITEM_THRESHOLD:
        return "same_mechanism_expression_gap"
    return "same_mechanism_boundary_review"


def _item_action(category: str) -> str:
    if category == "same_mechanism_expression_gap":
        return "generate_anchor_only"
    if category == "same_mechanism_boundary_review":
        return "review_boundary_before_generation"
    if category == "already_covered":
        return "preserve_baseline"
    return "use_as_negative_constraint"


def _case_recommended_action(items: list[ContractGapItem], boundary: dict[str, Any]) -> str:
    if any(item.action == "generate_anchor_only" for item in items):
        return "generate_anchor_only"
    if any(item.action == "review_boundary_before_generation" for item in items):
        return "review_boundary_before_generation"
    if boundary.get("action") == "avoid":
        return "avoid"
    return "preserve_baseline"


def _item_priority(item: ContractGapItem) -> int:
    priorities = {
        "same_mechanism_expression_gap": 0,
        "same_mechanism_boundary_review": 1,
        "foreign_public_entity_noise": 2,
        "blocked_by_score_feedback": 3,
        "mechanism_noise": 4,
        "already_covered": 5,
    }
    return priorities.get(item.category, 9)


def _case_priority(item: ContractGapCase) -> int:
    priorities = {
        "generate_anchor_only": 0,
        "review_boundary_before_generation": 1,
        "preserve_baseline": 2,
        "avoid": 3,
    }
    return priorities.get(item.recommended_action, 9)


def _score_item(answer_tokens: set[str], item_tokens: set[str]) -> float:
    if not item_tokens:
        return 0.0
    overlap = answer_tokens & item_tokens
    if not overlap:
        return 0.0
    return min(1.0, len(overlap) / max(1.0, min(10.0, len(item_tokens))))


def _score_boundaries(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = load_json(path)
    cases = payload.get("cases", []) if isinstance(payload, dict) else []
    return {
        str(item.get("case_id")): item
        for item in cases
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }


def _safe_hint(mechanisms: list[str], category: str) -> str:
    if category not in {"same_mechanism_expression_gap", "same_mechanism_boundary_review"}:
        return ""
    hints = [SAFE_HINTS[item] for item in mechanisms if item in SAFE_HINTS]
    return " ".join(hints[:2])


def _public_entity_tokens(tokens: set[str]) -> set[str]:
    return {item for item in tokens if item.startswith(PUBLIC_ENTITY_PREFIXES)}


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _list_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _case_suffix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[-1][-4:].lower()


def _top_counts(counts: dict[str, int], *, limit: int = 8) -> str:
    return ", ".join(f"{key}={value}" for key, value in Counter(counts).most_common(limit))
