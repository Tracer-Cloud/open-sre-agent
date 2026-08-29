from __future__ import annotations

import re
from collections.abc import Iterable

from tests.benchmarks.realrca_graph.alignment import assess_alignment
from tests.benchmarks.realrca_graph.answer_contract import assess_answer_contract
from tests.benchmarks.realrca_graph.features import token_features
from tests.benchmarks.realrca_graph.mechanism_terms import baseline_negated_mechanisms_in_text
from tests.benchmarks.realrca_graph.models import (
    CandidateAnswer,
    CandidateDecision,
    CandidateScore,
    EvidenceBundle,
    RootHypothesis,
)
from tests.benchmarks.realrca_graph.probe_feedback import CaseProbeFeedback

_REAL_TRACE_ID_RE = re.compile(r"^[0-9a-f]{24,40}$", re.IGNORECASE)
_LONG_TRACE_ID_RE = re.compile(r"\b[0-9a-f]{24,40}\b", re.IGNORECASE)
_EVAL_LEAKAGE_RE = re.compile(
    r"validation\s*案例|current[_ -]?best|benchmark|kg\s*候选|"
    r"图谱|graph\s*evidence|evidence[_ -]?bundle|证据包|"
    r"root_candidates|top_root_candidates|top_hypotheses|trace_list|"
    r"hypothes(?:is|es)|(?:^|[\s（(])h\d+(?:$|[\s）)])",
    re.IGNORECASE,
)
_NEGATIVE_CLAUSE_RE = re.compile(
    r"[^。；;\n]*(?:排除|不是|并非|未发现|无证据|不支持|而非|不将|无关|不能解释|无法解释|not\s+(?:the\s+)?root)[^。；;\n]*(?:[。；;\n]|$)",
    re.IGNORECASE,
)
_ROOT_FOCUS_BOUNDARY_RE = re.compile(
    r"(?:关键证据|证据[：:]|影响链路|传播链|处置建议|建议|排除项|不确定性|关键依据|evidence)",
    re.IGNORECASE,
)
_DIRECT_OBSERVATION_RE = re.compile(
    r"\b\d{2}:\d{2}(?::\d{2})?\b|第\s*\d+\s*行|couponCode|msgId|"
    r"ConsumeMessageThread|Exception|Trace\s*[0-9a-f]{12,}|trace\s*[0-9a-f]{12,}|"
    r"异常日志|错误日志|堆栈",
    re.IGNORECASE,
)
_MISSING_OBSERVATION_CLAIM_RE = re.compile(
    r"(?:trace|Trace|日志|异常日志|错误日志|链路).{0,36}"
    r"(?:未返回|未检索到|没有返回|缺失|为空|零条|0\s*条|无法直接|no\s+(?:log|trace))",
    re.IGNORECASE,
)


def _hypothesis_text(hypothesis: RootHypothesis) -> dict[str, object]:
    return {
        "kind": hypothesis.kind,
        "label": hypothesis.label,
        "reason": hypothesis.reason,
        "root_layer": hypothesis.root_layer,
        "entities": hypothesis.entities,
        "support": [item.to_dict() for item in hypothesis.support],
    }


def _hypothesis_core_text(hypothesis: RootHypothesis) -> dict[str, object]:
    return {
        "kind": hypothesis.kind,
        "label": hypothesis.label,
        "reason": hypothesis.reason,
        "root_layer": hypothesis.root_layer,
        "entities": hypothesis.entities,
    }


def _support_text(bundle: EvidenceBundle) -> dict[str, object]:
    return {
        "hypotheses": [_hypothesis_text(item) for item in bundle.hypotheses],
        "evidence": [item.to_dict() for item in bundle.evidence],
        "retrieval_summary": bundle.retrieval_summary,
    }


def _novelty(candidate_tokens: set[str], baseline_tokens: set[str]) -> float:
    if not candidate_tokens:
        return 0.0
    return round(1.0 - (len(candidate_tokens & baseline_tokens) / len(candidate_tokens)), 4)


def _compression_ratio(candidate: CandidateAnswer, baseline: CandidateAnswer) -> float:
    baseline_len = len(baseline.diagnosis_output.strip())
    if baseline_len == 0:
        return 1.0
    return len(candidate.diagnosis_output.strip()) / baseline_len


def affirmative_diagnosis_text(text: str) -> str:
    """Remove negative/exclusion clauses before positive root-evidence matching."""

    return _NEGATIVE_CLAUSE_RE.sub(" ", text)


def _root_focus_text(text: str) -> str:
    affirmative = affirmative_diagnosis_text(text)
    root_segment = _ROOT_FOCUS_BOUNDARY_RE.split(affirmative, maxsplit=1)[0]
    return root_segment[:600]


def _diagnosis_trace_ids(text: str) -> set[str]:
    return {match.group(0).lower() for match in _LONG_TRACE_ID_RE.finditer(text)}


def _real_trace_field(value: str) -> set[str]:
    normalized = value.strip().lower()
    return {normalized} if _REAL_TRACE_ID_RE.fullmatch(normalized) else set()


def _extra_trace_ids(candidate: CandidateAnswer, baseline: CandidateAnswer) -> list[str]:
    candidate_traces = _diagnosis_trace_ids(candidate.diagnosis_output)
    allowed_traces = (
        _diagnosis_trace_ids(baseline.diagnosis_output)
        | _real_trace_field(candidate.trace_id)
        | _real_trace_field(baseline.trace_id)
    )
    return sorted(candidate_traces - allowed_traces)


def _contradicts_baseline_direct_evidence(
    candidate: CandidateAnswer, baseline: CandidateAnswer
) -> bool:
    if not _DIRECT_OBSERVATION_RE.search(baseline.diagnosis_output):
        return False
    return bool(_MISSING_OBSERVATION_CLAIM_RE.search(candidate.diagnosis_output))


def _same_root_fault_domain(candidate: CandidateAnswer, baseline: CandidateAnswer) -> bool:
    candidate_tokens = _fault_domain_tokens(candidate.diagnosis_output)
    baseline_tokens = _fault_domain_tokens(baseline.diagnosis_output)
    if _shared_prefixed_token(candidate_tokens, baseline_tokens, "app:") and _shared_prefixed_token(
        candidate_tokens,
        baseline_tokens,
        "ip:",
    ):
        return True
    if _shared_prefixed_token(candidate_tokens, baseline_tokens, "rds:") or _shared_prefixed_token(
        candidate_tokens,
        baseline_tokens,
        "sql_table:",
    ):
        return True
    return (
        bool({"keyword:cache", "keyword:timeout"} <= candidate_tokens)
        and bool({"keyword:cache", "keyword:timeout"} <= baseline_tokens)
        and bool(_shared_cache_instances(candidate.diagnosis_output, baseline.diagnosis_output))
    )


def _same_root_evidence_expansion(
    *,
    candidate: CandidateAnswer,
    baseline: CandidateAnswer,
    retention: float,
    novelty: float,
) -> bool:
    return (
        retention >= 0.92
        and 0.38 <= novelty <= 0.6
        and _compression_ratio(candidate, baseline) >= 1.08
        and _same_root_fault_domain(candidate, baseline)
    )


def _fault_domain_tokens(text: str) -> set[str]:
    root_tokens = token_features(_root_focus_text(text))
    if _has_fault_domain_tokens(root_tokens):
        return root_tokens
    affirmative_tokens = token_features(affirmative_diagnosis_text(text)[:800])
    if _has_fault_domain_tokens(affirmative_tokens):
        return affirmative_tokens
    return token_features(text[:800])


def _has_fault_domain_tokens(tokens: set[str]) -> bool:
    return any(item.startswith(("app:", "ip:", "rds:", "sql_table:", "trace:")) for item in tokens)


def _shared_prefixed_token(left: set[str], right: set[str], prefix: str) -> bool:
    left_prefixed = {item for item in left if item.startswith(prefix)}
    right_prefixed = {item for item in right if item.startswith(prefix)}
    return bool(left_prefixed & right_prefixed)


def _shared_cache_instances(left: str, right: str) -> set[str]:
    left_tokens = token_features(left)
    right_tokens = token_features(right)
    left_values = {
        item.removeprefix("trace:")
        for item in left_tokens
        if item.startswith("trace:") and 12 <= len(item.removeprefix("trace:")) <= 24
    }
    right_values = {
        item.removeprefix("trace:")
        for item in right_tokens
        if item.startswith("trace:") and 12 <= len(item.removeprefix("trace:")) <= 24
    }
    return left_values & right_values


def score_candidate(
    candidate: CandidateAnswer,
    baseline: CandidateAnswer,
    bundle: EvidenceBundle,
    *,
    high_novelty_threshold: float = 0.62,
    probe_feedback: CaseProbeFeedback | None = None,
) -> CandidateScore:
    """Score one answer against graph evidence without reading hidden references."""

    answer_text = affirmative_diagnosis_text(candidate.diagnosis_output)
    answer_tokens = token_features(answer_text)
    root_focus_tokens = token_features(_root_focus_text(candidate.diagnosis_output))
    full_answer_tokens = token_features(candidate.diagnosis_output)
    baseline_tokens = token_features(baseline.diagnosis_output)
    best_rank: tuple[int, int, int, int, int, float] = (0, 0, 0, 0, 0, 0.0)
    best_hypothesis = ""
    best_label = ""
    best_modalities = 0
    best_hypothesis_overlap = 0
    contradiction_risk = False
    for hypothesis in bundle.hypotheses:
        core_tokens = token_features(_hypothesis_core_text(hypothesis))
        hypothesis_tokens = token_features(_hypothesis_text(hypothesis))
        core_overlap = answer_tokens & core_tokens
        root_focus_overlap = root_focus_tokens & core_tokens
        support_overlap = answer_tokens & hypothesis_tokens
        if not core_overlap and not support_overlap:
            continue
        modality_count = len(set(hypothesis.modalities))
        rank = (
            len(root_focus_overlap),
            len(core_overlap),
            len(support_overlap),
            0 if hypothesis.contradictions else 1,
            modality_count,
            hypothesis.score,
        )
        if rank > best_rank:
            best_rank = rank
            best_hypothesis = hypothesis.id
            best_label = hypothesis.label
            best_modalities = modality_count
            best_hypothesis_overlap = len(support_overlap)
            contradiction_risk = bool(hypothesis.contradictions)
    bundle_tokens = token_features(_support_text(bundle))
    evidence_overlap = answer_tokens & bundle_tokens
    novelty = _novelty(full_answer_tokens, baseline_tokens)
    is_baseline = candidate.source == baseline.source
    alignment = assess_alignment(candidate, baseline)
    contract = assess_answer_contract(candidate, bundle)
    graph_support = min(
        1.0,
        (best_hypothesis_overlap / 14.0)
        + (len(evidence_overlap) / 40.0)
        + (0.12 if best_modalities >= 2 else 0.0)
        + (
            0.08
            if candidate.trace_id and f"trace:{candidate.trace_id.lower()}" in bundle_tokens
            else 0.0
        ),
    )
    risk_flags: list[str] = []
    if best_modalities < 2:
        risk_flags.append("single_modality_or_untyped_support")
    if contradiction_risk:
        risk_flags.append("best_hypothesis_has_contradiction")
    if novelty > high_novelty_threshold:
        risk_flags.append("unsupported_high_novelty")
    if not is_baseline and alignment.retention < 0.45 and len(alignment.baseline_tokens) >= 3:
        risk_flags.append("drops_baseline_critical_tokens")
    if (
        not is_baseline
        and len(alignment.baseline_tokens) >= 3
        and alignment.retention < 0.82
        and _compression_ratio(candidate, baseline) < 0.82
    ):
        risk_flags.append("lossy_baseline_compression")
    if (
        not is_baseline
        and len(alignment.baseline_tokens) >= 3
        and alignment.retention < 0.75
        and novelty > 0.55
    ):
        risk_flags.append("rewrite_drops_baseline_context")
    if (
        not is_baseline
        and len(alignment.baseline_tokens) >= 4
        and alignment.retention < 0.82
        and novelty > 0.25
    ):
        risk_flags.append("partial_baseline_context_loss")
    if not is_baseline and alignment.retention >= 0.85 and novelty <= 0.5:
        risk_flags.append("likely_evidence_only_expansion")
    if not is_baseline and _same_root_evidence_expansion(
        candidate=candidate,
        baseline=baseline,
        retention=alignment.retention,
        novelty=novelty,
    ):
        risk_flags.append("same_root_evidence_expansion")
    if (
        not is_baseline
        and not _REAL_TRACE_ID_RE.fullmatch(candidate.trace_id)
        and candidate.trace_id.strip() != baseline.trace_id.strip()
    ):
        risk_flags.append("synthetic_or_invalid_trace_id")
    if not is_baseline and _extra_trace_ids(candidate, baseline):
        risk_flags.append("adds_secondary_trace_ids")
    if not is_baseline and _contradicts_baseline_direct_evidence(candidate, baseline):
        risk_flags.append("contradicts_baseline_direct_evidence")
    if not is_baseline and baseline_negated_mechanisms_in_text(
        baseline.diagnosis_output,
        _root_focus_text(candidate.diagnosis_output),
    ):
        risk_flags.append("uses_baseline_negated_mechanism")
    if not is_baseline and _EVAL_LEAKAGE_RE.search(candidate.diagnosis_output):
        risk_flags.append("evaluation_or_experiment_leakage_terms")
    if not is_baseline and contract.score < 0.62:
        risk_flags.append("answer_contract_incomplete")
    if not is_baseline and probe_feedback is not None:
        negative_record = probe_feedback.matching_negative(candidate.source)
        if negative_record is not None:
            risk_flags.append("negative_leaderboard_probe_family")
    if not best_hypothesis:
        risk_flags.append("no_hypothesis_overlap")
    return CandidateScore(
        source=candidate.source,
        graph_support=round(graph_support, 4),
        answer_contract_score=contract.score,
        best_hypothesis_id=best_hypothesis,
        best_hypothesis_label=best_label,
        overlap_count=len(evidence_overlap),
        modality_count=best_modalities,
        novelty=novelty,
        baseline_retention=alignment.retention,
        dropped_baseline_tokens=alignment.dropped_tokens[:16],
        risk_flags=risk_flags,
        contract_flags=contract.flags,
    )


def decide_candidate(
    baseline: CandidateAnswer,
    candidates: Iterable[CandidateAnswer],
    bundle: EvidenceBundle,
    *,
    min_support: float = 0.58,
    min_margin: float = 0.08,
    min_modalities: int = 2,
    max_novelty: float = 0.62,
    probe_feedback: CaseProbeFeedback | None = None,
) -> CandidateDecision:
    """Conservatively choose a candidate answer, defaulting to the baseline."""

    scored = [score_candidate(baseline, baseline, bundle, high_novelty_threshold=max_novelty)]
    for candidate in candidates:
        if candidate.case_id != baseline.case_id:
            continue
        if candidate.source == baseline.source:
            continue
        scored.append(
            score_candidate(
                candidate,
                baseline,
                bundle,
                high_novelty_threshold=max_novelty,
                probe_feedback=probe_feedback,
            )
        )
    baseline_score = scored[0]
    replacement_scores = sorted(
        scored[1:],
        key=lambda item: (
            -item.graph_support,
            -item.answer_contract_score,
            -item.modality_count,
            item.novelty,
            item.source,
        ),
    )
    stable_baseline = not baseline_score.risk_flags and baseline_score.graph_support >= min_support
    candidate_by_source = {
        candidate.source: candidate
        for candidate in candidates
        if candidate.case_id == baseline.case_id and candidate.source != baseline.source
    }
    for score in replacement_scores:
        if stable_baseline and score.graph_support < baseline_score.graph_support + max(
            0.25, min_margin
        ):
            continue
        if score.graph_support < min_support:
            continue
        if score.graph_support < baseline_score.graph_support + min_margin:
            continue
        if score.modality_count < min_modalities:
            continue
        if score.risk_flags:
            continue
        selected = candidate_by_source[score.source]
        return CandidateDecision(
            case_id=baseline.case_id,
            selected=selected,
            baseline=baseline,
            accepted_replacement=True,
            reason=(
                f"accepted {score.source}: graph_support={score.graph_support} exceeds "
                f"baseline={baseline_score.graph_support} with {score.modality_count} modalities"
            ),
            scores=scored,
        )
    best_candidate = replacement_scores[0] if replacement_scores else None
    if best_candidate is None:
        reason = "kept baseline: no candidate rows for this case"
    elif stable_baseline:
        reason = (
            f"kept baseline: stable risk-free baseline support={baseline_score.graph_support}; "
            f"best candidate {best_candidate.source} support={best_candidate.graph_support}, "
            f"required stable-baseline margin={max(0.25, min_margin)}"
        )
    else:
        reason = (
            f"kept baseline: best candidate {best_candidate.source} support="
            f"{best_candidate.graph_support}, baseline={baseline_score.graph_support}, "
            f"risks={best_candidate.risk_flags}"
        )
    return CandidateDecision(
        case_id=baseline.case_id,
        selected=baseline,
        baseline=baseline,
        accepted_replacement=False,
        reason=reason,
        scores=scored,
    )


def selected_rows(decisions: Iterable[CandidateDecision]) -> list[dict[str, str]]:
    return [decision.selected.to_result_row() for decision in decisions]
