from __future__ import annotations

import json
import re
import textwrap
from dataclasses import asdict, dataclass
from typing import Any

from tests.benchmarks.realrca_graph.answer_contract import prompt_contract
from tests.benchmarks.realrca_graph.bundle import bundle_prompt_context
from tests.benchmarks.realrca_graph.features import clip_text
from tests.benchmarks.realrca_graph.models import CandidateAnswer, CandidateScore, EvidenceBundle

HARD_RISK_FLAGS = {
    "synthetic_or_invalid_trace_id",
    "adds_secondary_trace_ids",
    "drops_baseline_critical_tokens",
    "likely_evidence_only_expansion",
    "candidate_from_negative_probe_family",
    "rewrite_drops_baseline_context",
    "lossy_baseline_compression",
    "same_root_evidence_expansion",
    "uses_baseline_negated_mechanism",
}
STABLE_BASELINE_MIN_SUPPORT = 0.58
STABLE_BASELINE_MIN_MARGIN = 0.25
PROCESS_RISK_PREFIXES = (
    "evaluation_leakage",
    "graph_process",
    "evidence_bundle_process",
)


@dataclass(frozen=True)
class PairwiseVerifierPackage:
    """Visible package for one baseline-vs-candidate RCA comparison."""

    case: dict[str, Any]
    current_answer: dict[str, str]
    challenger_answer: dict[str, str]
    observations: dict[str, Any]
    answer_contract: dict[str, Any]
    current_score: dict[str, Any]
    challenger_score: dict[str, Any]
    previous_probe_agents: list[str]
    strategy_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PairwiseVerifierDecision:
    """Parsed LLM pairwise verifier decision."""

    case_id: str
    verdict: str
    confidence: float
    baseline_has_material_error: bool
    candidate_preserves_baseline_root: bool
    reason: str
    supporting_observations: list[str]
    failure_modes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_pairwise_verifier_package(
    *,
    case: dict[str, Any],
    baseline: CandidateAnswer,
    candidate: CandidateAnswer,
    bundle: EvidenceBundle,
    baseline_score: CandidateScore,
    candidate_score: CandidateScore,
    previous_probe_agents: list[str],
    strategy_hint: str = "",
) -> PairwiseVerifierPackage:
    """Build a no-reference prompt package for pairwise RCA answer verification."""

    observations = bundle_prompt_context(bundle, hypothesis_limit=6)
    for hypothesis in observations.get("top_hypotheses", []):
        if not isinstance(hypothesis, dict):
            continue
        hypothesis.pop("id", None)
        for item in hypothesis.get("support", []):
            if isinstance(item, dict):
                item.pop("id", None)
                item.pop("raw_ref", None)
                item["summary"] = clip_text(item.get("summary", ""), 420)
    return PairwiseVerifierPackage(
        case=_visible_case(case),
        current_answer=baseline.to_result_row(),
        challenger_answer=candidate.to_result_row(),
        observations=observations,
        answer_contract=prompt_contract(bundle),
        current_score=baseline_score.to_dict(),
        challenger_score=candidate_score.to_dict(),
        previous_probe_agents=sorted(set(previous_probe_agents)),
        strategy_hint=strategy_hint.strip(),
    )


def render_pairwise_verifier_prompt(package: PairwiseVerifierPackage) -> str:
    """Render a strict pairwise verifier prompt for DMA."""

    payload = json.dumps(package.to_dict(), ensure_ascii=False, indent=2)
    case_id = package.current_answer["case_id"]
    strategy_hint = _strategy_hint_block(package.strategy_hint)
    return textwrap.dedent(
        f"""
        你是 RealRCA-Bench 的 RCA pairwise verifier，只判断 challenger_answer 是否比 current_answer 更值得提交。

        数据边界：
        - 禁止读取 hidden reference、root_cause_chain、答案文件、提交反馈或任何泄露数据。
        - 只能使用下面 JSON package 中的可见 case、观测证据、两个答案和确定性分数。
        - previous_probe_agents 只表示历史试验名称，不代表正确答案。

        判断标准：
        1. 先判断 current_answer 是否存在实质错误：根因实体错、触发机制错、传播链方向错、trace_id 无效或关键证据矛盾。
        2. challenger_answer 只有在修正上述实质错误、且保留 current_answer 中被证据支持的关键实体时才应胜出。
        3. 只增加更多证据、换一种说法、压缩/扩写、或把同一根因写得更顺，不算值得替换。
        4. 若 challenger_answer 引入多个额外 trace_id、图谱/候选/证据包等过程词、或丢失服务/接口/异常/SQL/RDS/主 trace，必须选择 current。
        5. 需要偏保守：不确定时选 current。
        6. 输出必须是 JSON，第一个字符是 {{，最后一个字符是 }}。
        {strategy_hint}

        输出 JSON Schema：
        {{
          "case_id": "{case_id}",
          "verdict": "current | challenger | tie",
          "confidence": 0.0,
          "baseline_has_material_error": false,
          "candidate_preserves_baseline_root": true,
          "reason": "2-4 句说明",
          "supporting_observations": ["最多 4 条可见证据摘要"],
          "failure_modes": ["若不选 challenger，列出主要原因"]
        }}

        JSON package:
        {payload}
        """
    ).strip()


def extract_pairwise_verifier_result(text: str) -> dict[str, Any] | None:
    """Extract the final JSON verifier object from a DMA response."""

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for match in re.finditer(r"\{", cleaned):
        try:
            value, _offset = decoder.raw_decode(cleaned[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "verdict" in value:
            candidates.append(value)
    return candidates[-1] if candidates else None


def parse_pairwise_verifier_decision(
    case_id: str,
    result: dict[str, Any],
) -> PairwiseVerifierDecision:
    """Validate and normalize one LLM verifier result."""

    verdict = str(result.get("verdict") or "").strip().lower()
    if verdict not in {"current", "challenger", "tie"}:
        raise ValueError(f"invalid verdict: {verdict}")
    confidence = _float_in_range(result.get("confidence"))
    actual_case_id = str(result.get("case_id") or "").strip()
    if actual_case_id != case_id:
        raise ValueError(f"case_id mismatch: {actual_case_id} != {case_id}")
    return PairwiseVerifierDecision(
        case_id=case_id,
        verdict=verdict,
        confidence=confidence,
        baseline_has_material_error=bool(result.get("baseline_has_material_error")),
        candidate_preserves_baseline_root=bool(result.get("candidate_preserves_baseline_root")),
        reason=clip_text(str(result.get("reason") or ""), 700),
        supporting_observations=_string_list(result.get("supporting_observations"), limit=4),
        failure_modes=_string_list(result.get("failure_modes"), limit=6),
    )


def has_hard_risk(score: CandidateScore) -> bool:
    """Return whether deterministic verifier flags make a replacement unsafe."""

    for flag in score.risk_flags:
        if flag in HARD_RISK_FLAGS:
            return True
        if any(flag.startswith(prefix) for prefix in PROCESS_RISK_PREFIXES):
            return True
    return False


def should_accept_pairwise_decision(
    *,
    decision: PairwiseVerifierDecision,
    baseline_score: CandidateScore,
    candidate_score: CandidateScore,
    min_confidence: float = 0.72,
    min_support_margin: float = 0.05,
) -> tuple[bool, str]:
    """Apply deterministic hard gates around an LLM pairwise verdict."""

    if decision.verdict != "challenger":
        return False, f"llm_verdict_{decision.verdict}"
    if decision.confidence < min_confidence:
        return False, f"llm_confidence_below_{min_confidence:.2f}"
    if has_hard_risk(candidate_score):
        return False, "candidate_has_hard_risk"
    support_margin = candidate_score.graph_support - baseline_score.graph_support
    stable_baseline = (
        not baseline_score.risk_flags
        and baseline_score.graph_support >= STABLE_BASELINE_MIN_SUPPORT
    )
    if stable_baseline and support_margin < max(STABLE_BASELINE_MIN_MARGIN, min_support_margin):
        return False, "stable_baseline_requires_large_support_margin"
    if support_margin < min_support_margin and not decision.baseline_has_material_error:
        return False, "no_material_error_or_support_margin"
    if not decision.candidate_preserves_baseline_root and candidate_score.baseline_retention < 0.9:
        return False, "candidate_does_not_preserve_supported_baseline_root"
    return True, "accepted_by_pairwise_verifier"


def _visible_case(case: dict[str, Any]) -> dict[str, Any]:
    visible = dict(case)
    for key in ("root_cause_chain", "reference", "name"):
        visible.pop(key, None)
    meta = visible.get("meta")
    if isinstance(meta, dict):
        clean_meta = dict(meta)
        clean_meta.pop("name", None)
        visible["meta"] = clean_meta
    return visible


def _strategy_hint_block(strategy_hint: str) -> str:
    if not strategy_hint.strip():
        return ""
    return "\n        本轮额外策略约束：\n        - " + strategy_hint.strip()


def _float_in_range(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be numeric") from exc
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError(f"confidence out of range: {parsed}")
    return parsed


def _string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = clip_text(str(item).strip(), 240)
        if text:
            output.append(text)
        if len(output) >= limit:
            break
    return output
