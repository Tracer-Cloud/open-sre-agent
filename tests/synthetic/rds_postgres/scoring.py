"""Pure scoring functions for the synthetic RDS benchmark suite.

This module is free of ``app.*`` imports so scoring logic can be unit-tested
without importing LangGraph or any heavy runtime dependencies.

Dataclasses and functions in this module are re-exported from run_suite.py
for backward compatibility with existing import sites.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tests.synthetic.rds_postgres.evidence_sources import missing_sources as _evidence_missing
from tests.synthetic.rds_postgres.scenario_loader import ScenarioFixture

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrajectoryScore:
    actual_sequence: list[str]  # flattened actions from executed_hypotheses
    expected_sequence: list[str]  # from answer_key.optimal_trajectory
    loops_used: int
    reported_loops_used: int
    loop_count_consistent: bool
    actions_per_loop: list[int]
    max_loops: int
    sequencing_ok: bool  # all expected actions appear in actual (set membership)
    calibration_ok: bool  # loops_used <= max_loops
    trajectory_budget_ok: bool  # no extra actions beyond expected trajectory
    extra_actions_count: int
    efficiency_score: float  # mean(sequencing_ok, calibration_ok, trajectory_budget_ok)


@dataclass(frozen=True)
class FailureDetail:
    code: str
    detail: str


@dataclass(frozen=True)
class GateResult:
    status: str
    threshold: str
    actual: str


@dataclass(frozen=True)
class ReasoningScore:
    """Axis 2 adversarial reasoning quality score.

    ruling_out_ok: every ruling_out_keywords token was found in agent output.
    queries_ok: every required_queries metric name was requested via query_timeseries.
    reasoning_score: mean(ruling_out_ok, queries_ok); 1.0 = full pass.
    """

    ruling_out_ok: bool
    queries_ok: bool
    missing_ruling_out: list[str]
    missing_queries: list[str]
    reasoning_score: float


@dataclass(frozen=True)
class ScenarioScore:
    scenario_id: str
    passed: bool
    root_cause_present: bool
    expected_category: str
    accepted_categories: tuple[str, ...]
    actual_category: str
    missing_keywords: list[str]
    matched_keywords: list[str]
    exact_missing_keywords: list[str] = field(default_factory=list)
    exact_matched_keywords: list[str] = field(default_factory=list)
    semantic_missing_keywords: list[str] = field(default_factory=list)
    semantic_matched_keywords: list[str] = field(default_factory=list)
    exact_keyword_match: bool = False
    semantic_keyword_match: bool = False
    normalization_used: list[str] = field(default_factory=list)
    gates: dict[str, GateResult] = field(default_factory=dict)
    failure_reasons: list[FailureDetail] = field(default_factory=list)
    root_cause: str = ""
    failure_reason: str = ""
    trajectory: TrajectoryScore | None = None
    reasoning: ReasoningScore | None = None


# ---------------------------------------------------------------------------
# Gate configuration
# ---------------------------------------------------------------------------

_REQUIRED_GATE_NAMES = {
    "category_match",
    "required_keyword_match",
    "required_evidence_sources",
    "trajectory_budget",
    "forbidden_category_clear",
    "forbidden_keyword_clear",
    "failover_event_reasoning",
    "trajectory_policy",
}


def _all_required_gates_pass(gates: dict[str, GateResult]) -> bool:
    for gate_name, gate in gates.items():
        if gate_name not in _REQUIRED_GATE_NAMES:
            continue
        if gate.status != "pass":
            return False
    return True


# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _normalize_query_token(value: str) -> str:
    return _normalize_text(value).replace(" ", "_").replace("-", "_")


def _keyword_match_details(normalized_output: str, keyword: str) -> tuple[bool, str, str | None]:
    normalized_keyword = _normalize_text(keyword)
    if normalized_keyword in normalized_output:
        return True, "exact_phrase", None

    keyword_aliases = {
        "dbconnections": (
            "databaseconnections",
            "db connections",
            "database connections",
        ),
        "max_connections": (
            "maximum allowed connections",
            "max allowed connections",
            "allowed connections",
            "connection slots",
        ),
        "performanceinsights": (
            "top sql activity",
            "avg load",
            "aas",
            "active sessions",
            "db load",
        ),
        "client sessions": (
            "client session",
            "idle database sessions",
            "database sessions",
        ),
        "idle": (
            "clientread",
            "waiting for client response",
            "sessions remain open",
            "open sessions",
        ),
        "write-heavyworkload": (
            "write heavy workload",
            "write-heavy update",
            "update-heavy workload",
            "heavy update workload",
        ),
        "replicationlag": (
            "replica lag",
            "replicalag",
            "replication delay",
        ),
        "causallyindependent": (
            "red herring",
            "not the root cause",
            "unrelated confounder",
            "no causal relationship",
            "not causally related",
            "coincidental",
            "unrelated to the lag",
            "not related to replication lag",
        ),
    }
    for alias in keyword_aliases.get(normalized_keyword.replace(" ", ""), ()):
        if _normalize_text(alias) in normalized_output:
            return True, "alias_lookup", alias

    keyword_tokens = set(re.findall(r"[a-z0-9]+", normalized_keyword))
    if not keyword_tokens:
        return False, "none", None

    output_tokens = set(re.findall(r"[a-z0-9]+", normalized_output))
    if keyword_tokens.issubset(output_tokens):
        return True, "token_subset", None
    return False, "none", None


def _matches_required_keyword(normalized_output: str, keyword: str) -> bool:
    semantic_match, _, _ = _keyword_match_details(normalized_output, keyword)
    return semantic_match


def _matches_required_keyword_exact(normalized_output: str, keyword: str) -> bool:
    normalized_keyword = _normalize_text(keyword)
    return bool(normalized_keyword) and normalized_keyword in normalized_output


def _scored_output_text(final_state: dict[str, Any]) -> str:
    """Return the broadest textual output we should grade for synthetic scenarios."""
    return " ".join(
        [
            str(final_state.get("root_cause") or ""),
            " ".join(claim.get("claim", "") for claim in final_state.get("validated_claims", [])),
            " ".join(
                claim.get("claim", "") for claim in final_state.get("non_validated_claims", [])
            ),
            " ".join(final_state.get("causal_chain", [])),
            str(final_state.get("report") or ""),
            str((final_state.get("problem_report") or {}).get("report_md") or ""),
        ]
    )


# ---------------------------------------------------------------------------
# Pure scoring functions
# ---------------------------------------------------------------------------


def _accepted_root_cause_categories(fixture: ScenarioFixture) -> frozenset[str]:
    """Categories that satisfy the synthetic suite category gate."""
    key = fixture.answer_key
    accepted: set[str] = {key.root_cause_category}
    accepted.update(key.equivalent_root_cause_categories)
    return frozenset(accepted)


def score_trajectory(
    fixture: ScenarioFixture,
    final_state: dict[str, Any],
) -> TrajectoryScore | None:
    """Score the agent's investigation trajectory against the expected sequence.

    Returns None when no optimal_trajectory is declared for the scenario.
    """
    expected = list(fixture.answer_key.optimal_trajectory)
    if not expected:
        return None

    max_loops = fixture.answer_key.max_investigation_loops

    executed_hypotheses: list[dict[str, Any]] = final_state.get("executed_hypotheses") or []
    actual_sequence: list[str] = []
    actions_per_loop: list[int] = []
    for hyp in executed_hypotheses:
        actions = [str(action) for action in hyp.get("actions", [])]
        actions_per_loop.append(len(actions))
        actual_sequence.extend(actions)

    action_loops_used = len(executed_hypotheses)
    reported_loops_used = int(final_state.get("investigation_loop_count") or action_loops_used)
    loop_count_consistent = reported_loops_used == action_loops_used

    sequencing_ok = set(expected) <= set(actual_sequence)
    calibration_ok = action_loops_used <= max_loops
    extra_actions_count = len([action for action in actual_sequence if action not in set(expected)])
    trajectory_budget_ok = extra_actions_count == 0
    efficiency_score = (int(sequencing_ok) + int(calibration_ok) + int(trajectory_budget_ok)) / 3.0

    return TrajectoryScore(
        actual_sequence=actual_sequence,
        expected_sequence=expected,
        loops_used=action_loops_used,
        reported_loops_used=reported_loops_used,
        loop_count_consistent=loop_count_consistent,
        actions_per_loop=actions_per_loop,
        max_loops=max_loops,
        sequencing_ok=sequencing_ok,
        calibration_ok=calibration_ok,
        trajectory_budget_ok=trajectory_budget_ok,
        extra_actions_count=extra_actions_count,
        efficiency_score=efficiency_score,
    )


def score_reasoning(
    fixture: ScenarioFixture,
    final_state: dict[str, Any],
    queried_metrics: list[str] | None = None,
) -> ReasoningScore | None:
    """Score Axis 2 adversarial reasoning quality.

    Returns None when neither ruling_out_keywords nor required_queries are
    declared for the scenario.
    """
    has_ruling_out = bool(fixture.answer_key.ruling_out_keywords)
    has_required_queries = bool(fixture.answer_key.required_queries)
    if not has_ruling_out and not has_required_queries:
        return None

    evidence_text = _scored_output_text(final_state)
    normalized_output = _normalize_text(evidence_text)

    missing_ruling_out: list[str] = []
    if has_ruling_out:
        for token in fixture.answer_key.ruling_out_keywords:
            if not _matches_required_keyword(normalized_output, token):
                missing_ruling_out.append(token)

    missing_queries: list[str] = []
    if has_required_queries:
        audited = {_normalize_query_token(item) for item in (queried_metrics or [])}
        for required in fixture.answer_key.required_queries:
            token = _normalize_query_token(required)
            if not any(token in q for q in audited):
                missing_queries.append(required)

    ruling_out_ok = not missing_ruling_out
    queries_ok = not missing_queries
    reasoning_score = (int(ruling_out_ok) + int(queries_ok)) / 2.0

    return ReasoningScore(
        ruling_out_ok=ruling_out_ok,
        queries_ok=queries_ok,
        missing_ruling_out=missing_ruling_out,
        missing_queries=missing_queries,
        reasoning_score=reasoning_score,
    )


def score_result(
    fixture: ScenarioFixture,
    final_state: dict[str, Any],
    queried_metrics: list[str] | None = None,
) -> ScenarioScore:
    root_cause = str(final_state.get("root_cause") or "").strip()
    actual_category = str(final_state.get("root_cause_category") or "unknown").strip()
    root_cause_present = bool(root_cause and root_cause.lower() != "unable to determine root cause")

    evidence_text = _scored_output_text(final_state)
    normalized_output = _normalize_text(evidence_text)

    exact_matched_keywords = [
        keyword
        for keyword in fixture.answer_key.required_keywords
        if _matches_required_keyword_exact(normalized_output, keyword)
    ]
    exact_missing_keywords = [
        keyword
        for keyword in fixture.answer_key.required_keywords
        if keyword not in exact_matched_keywords
    ]
    semantic_matched_keywords: list[str] = []
    semantic_missing_keywords: list[str] = []
    normalization_used: set[str] = {"casefold_whitespace_normalization"}
    for keyword in fixture.answer_key.required_keywords:
        semantic_match, match_mode, _matched_alias = _keyword_match_details(
            normalized_output, keyword
        )
        if semantic_match:
            semantic_matched_keywords.append(keyword)
            normalization_used.add(match_mode)
        else:
            semantic_missing_keywords.append(keyword)

    matched_keywords = list(semantic_matched_keywords)
    missing_keywords = list(semantic_missing_keywords)
    exact_keyword_match = not exact_missing_keywords
    semantic_keyword_match = not semantic_missing_keywords

    answer_key = fixture.answer_key
    accepted_cats = _accepted_root_cause_categories(fixture)
    accepted_sorted = tuple(sorted(accepted_cats))
    trajectory = score_trajectory(fixture, final_state)
    reasoning = score_reasoning(fixture, final_state, queried_metrics)
    failures: list[FailureDetail] = []

    gates: dict[str, GateResult] = {}

    def _mark_gate(name: str, passed: bool, threshold: str, actual: str) -> None:
        gates[name] = GateResult(
            status="pass" if passed else "fail",
            threshold=threshold,
            actual=actual,
        )

    # 1. Category match
    if not root_cause_present:
        failures.append(FailureDetail(code="NO_ROOT_CAUSE", detail="no root cause in output"))
    elif actual_category not in accepted_cats:
        failures.append(
            FailureDetail(
                code="WRONG_CATEGORY",
                detail=(
                    f"wrong category: got {actual_category!r}, expected one of "
                    f"{sorted(accepted_cats)!r}"
                ),
            )
        )
    _mark_gate(
        "category_match",
        root_cause_present and actual_category in accepted_cats,
        f"actual_category in {sorted(accepted_cats)!r}",
        f"root_cause_present={root_cause_present}, actual_category={actual_category!r}",
    )

    if semantic_missing_keywords:
        failures.append(
            FailureDetail(
                code="MISSING_REQUIRED_KEYWORD",
                detail=f"missing required keywords: {semantic_missing_keywords}",
            )
        )
    _mark_gate(
        "required_keyword_match",
        semantic_keyword_match,
        "all required keywords matched (semantic)",
        (f"missing_semantic={semantic_missing_keywords}, missing_exact={exact_missing_keywords}"),
    )

    _mark_gate(
        "exact_keyword_match",
        exact_keyword_match,
        "all required keywords matched verbatim",
        f"missing_exact={exact_missing_keywords}",
    )
    _mark_gate(
        "semantic_keyword_match",
        semantic_keyword_match,
        "all required keywords matched semantically",
        f"missing_semantic={semantic_missing_keywords}",
    )

    # 2. Forbidden category check
    forbidden_category_hit = bool(
        answer_key.forbidden_categories and actual_category in answer_key.forbidden_categories
    )
    if forbidden_category_hit:
        failures.append(
            FailureDetail(
                code="FORBIDDEN_CATEGORY_PRESENT",
                detail=f"forbidden category in output: {actual_category!r}",
            )
        )
    _mark_gate(
        "forbidden_category_clear",
        not forbidden_category_hit,
        "actual_category not in forbidden_categories",
        f"actual_category={actual_category!r}, forbidden={answer_key.forbidden_categories}",
    )

    # 3. Forbidden keyword check
    forbidden_hits: list[str] = []
    if answer_key.forbidden_keywords:
        forbidden_hits = [
            kw for kw in answer_key.forbidden_keywords if _normalize_text(kw) in normalized_output
        ]
        if forbidden_hits:
            failures.append(
                FailureDetail(
                    code="FORBIDDEN_KEYWORD_PRESENT",
                    detail=f"forbidden keywords in output: {forbidden_hits}",
                )
            )
    _mark_gate(
        "forbidden_keyword_clear",
        not forbidden_hits,
        "no forbidden keywords appear in graded output text",
        f"forbidden_hits={forbidden_hits}",
    )

    # 4. Evidence path check via semantic predicates
    missing_required_evidence: list[str] = []
    if answer_key.required_evidence_sources:
        missing_required_evidence = _evidence_missing(
            final_state, list(answer_key.required_evidence_sources)
        )

    if missing_required_evidence:
        failures.append(
            FailureDetail(
                code="MISSING_REQUIRED_EVIDENCE_SOURCE",
                detail=f"required evidence not gathered: {missing_required_evidence}",
            )
        )
    _mark_gate(
        "required_evidence_sources",
        not missing_required_evidence,
        "all required evidence sources populated",
        f"missing_required_evidence={missing_required_evidence}",
    )

    _mark_gate(
        "trajectory_budget",
        trajectory.trajectory_budget_ok if trajectory is not None else True,
        "extra_actions_count == 0",
        (
            f"extra_actions_count={trajectory.extra_actions_count}"
            if trajectory is not None
            else "not_applicable"
        ),
    )

    # 5. Failover event reasoning check
    failover_required_tokens = {
        "primary evidence source",
        "failover initiated",
        "failover in progress",
        "failover completed",
        "instance available",
    }
    normalized_required_keywords = {
        _normalize_text(keyword) for keyword in answer_key.required_keywords
    }
    requires_failover_event_reasoning = failover_required_tokens.issubset(
        normalized_required_keywords
    )

    if requires_failover_event_reasoning:
        root_cause_text = _normalize_text(root_cause)
        validated_text = _normalize_text(
            " ".join(claim.get("claim", "") for claim in final_state.get("validated_claims", []))
        )
        causal_chain_text = _normalize_text(" ".join(final_state.get("causal_chain", [])))

        reasoning_text = " ".join([root_cause_text, validated_text, causal_chain_text])

        mentions_event_reasoning = (
            "rds" in reasoning_text
            and ("event" in reasoning_text or "timeline" in reasoning_text)
            and "primary evidence source" in reasoning_text
        )

        if not mentions_event_reasoning:
            failures.append(
                FailureDetail(
                    code="FAILOVER_REASONING_NOT_PRIMARY",
                    detail="RDS events gathered but not used as primary reasoning signal",
                )
            )

        required_sequence_tokens = (
            "failover initiated",
            "failover in progress",
            "failover completed",
            "instance available",
        )

        sequence_present = all(token in reasoning_text for token in required_sequence_tokens)

        if not sequence_present:
            failures.append(
                FailureDetail(
                    code="FAILOVER_SEQUENCE_INCOMPLETE",
                    detail="RDS event sequence not explicitly listed in required form",
                )
            )
        _mark_gate(
            "failover_event_reasoning",
            mentions_event_reasoning and sequence_present,
            "mentions primary RDS event reasoning and full failover sequence tokens",
            (
                f"mentions_event_reasoning={mentions_event_reasoning}, "
                f"sequence_present={sequence_present}"
            ),
        )
    else:
        _mark_gate(
            "failover_event_reasoning",
            True,
            "not required unless failover sequence keywords are in answer key",
            "not_applicable",
        )

    passed = _all_required_gates_pass(gates) and not failures
    failure_reason = "; ".join(detail.detail for detail in failures)
    return ScenarioScore(
        scenario_id=fixture.scenario_id,
        passed=passed,
        root_cause_present=root_cause_present,
        expected_category=fixture.answer_key.root_cause_category,
        accepted_categories=accepted_sorted,
        actual_category=actual_category,
        missing_keywords=missing_keywords,
        matched_keywords=matched_keywords,
        exact_missing_keywords=exact_missing_keywords,
        exact_matched_keywords=exact_matched_keywords,
        semantic_missing_keywords=semantic_missing_keywords,
        semantic_matched_keywords=semantic_matched_keywords,
        exact_keyword_match=exact_keyword_match,
        semantic_keyword_match=semantic_keyword_match,
        normalization_used=sorted(normalization_used),
        gates=gates,
        failure_reasons=failures,
        root_cause=root_cause,
        failure_reason=failure_reason,
        trajectory=trajectory,
        reasoning=reasoning,
    )
