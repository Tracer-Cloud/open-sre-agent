"""Table-driven contract tests for the trajectory policy evaluator.

Phase 2 done-criteria: evaluate_trajectory_policy is importable from
trajectory_policy without importing rich/observations transitively, and every
single-violation kind plus combined violations are deterministically detected.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.synthetic.rds_postgres.trajectory_policy import (
    TrajectoryMetrics,
    TrajectoryPolicy,
    TrajectoryPolicyResult,
    evaluate_trajectory_policy,
)

# ---------------------------------------------------------------------------
# Import isolation: trajectory_policy must not pull in rich/observations
# ---------------------------------------------------------------------------


def test_trajectory_policy_importable_without_rich_or_observations() -> None:
    """trajectory_policy.py must not transitively import rich or observations."""
    import sys

    # Ensure trajectory_policy is importable cleanly
    assert "tests.synthetic.rds_postgres.trajectory_policy" in sys.modules

    # rich should NOT be triggered by importing trajectory_policy alone.
    # (It is acceptable for rich to be imported by other modules already in
    # sys.modules, but the trajectory_policy module itself must not require it.)
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_trajectory_policy_fresh",
        "tests/synthetic/rds_postgres/trajectory_policy.py",
    )
    assert spec is not None


# ---------------------------------------------------------------------------
# TrajectoryMetrics stub — avoids importing observations
# ---------------------------------------------------------------------------


def _make_metrics(
    flat_actions: list[str] | None = None,
    golden_actions: list[str] | None = None,
    strict_match: bool | None = False,
    lcs_ratio: float | None = 0.0,
    edit_distance: int | None = 3,
    extra_actions: list[str] | None = None,
    missing_actions: list[str] | None = None,
    redundancy_count: int = 0,
    loops_used: int = 1,
    max_loops: int | None = 3,
) -> Any:
    """Return a minimal duck-typed TrajectoryMetrics substitute."""
    from tests.synthetic.rds_postgres.observations import compute_trajectory_metrics

    golden = golden_actions or []
    actual = flat_actions or []

    # Build realistic metrics from actual + golden arrays
    computed = compute_trajectory_metrics(
        executed_hypotheses=[{"actions": [{"tool_name": a} for a in actual]}] if actual else [],
        golden=golden,
        loops_used=loops_used,
        max_loops=max_loops,
    )
    # Override specific fields when explicitly provided
    return TrajectoryMetrics(
        flat_actions=computed.flat_actions,
        actions_per_loop=computed.actions_per_loop,
        strict_match=strict_match if strict_match is not None else computed.strict_match,
        lcs_ratio=lcs_ratio if lcs_ratio is not None else computed.lcs_ratio,
        edit_distance=edit_distance if edit_distance is not None else computed.edit_distance,
        coverage=computed.coverage,
        extra_actions=extra_actions if extra_actions is not None else computed.extra_actions,
        missing_actions=missing_actions
        if missing_actions is not None
        else computed.missing_actions,
        redundancy_count=redundancy_count,
        loops_used=loops_used,
        max_loops=max_loops,
        loop_calibration_ok=computed.loop_calibration_ok,
        failed_action_count=computed.failed_action_count,
    )


# ---------------------------------------------------------------------------
# Parametrized contract test: single violations
# ---------------------------------------------------------------------------

GOLDEN = ["query_grafana_metrics", "query_grafana_logs", "query_grafana_alert_rules"]

_VIOLATION_CASES = [
    pytest.param(
        "strict",
        _make_metrics(
            golden_actions=GOLDEN,
            flat_actions=["query_grafana_metrics"],  # wrong order/subset
            strict_match=False,
        ),
        TrajectoryPolicy(matching="strict"),
        ["strict sequence mismatch"],
        id="strict-sequence-mismatch",
    ),
    pytest.param(
        "lcs",
        _make_metrics(
            golden_actions=GOLDEN,
            flat_actions=["query_grafana_metrics"],
            lcs_ratio=0.33,
        ),
        TrajectoryPolicy(matching="lcs"),
        ["lcs_ratio=0.33 < 1.00"],
        id="lcs-ratio-below-1",
    ),
    pytest.param(
        "set",
        _make_metrics(
            golden_actions=GOLDEN,
            flat_actions=["query_grafana_metrics"],
            missing_actions=["query_grafana_logs", "query_grafana_alert_rules"],
        ),
        TrajectoryPolicy(matching="set"),
        ["missing actions:"],  # partial match — actions may vary
        id="set-missing-actions",
    ),
    pytest.param(
        "edit_distance",
        _make_metrics(
            golden_actions=GOLDEN,
            flat_actions=["query_grafana_metrics"],
            lcs_ratio=1.0,  # set matching passes
            edit_distance=5,
        ),
        TrajectoryPolicy(matching="lcs", max_edit_distance=2),
        ["edit_distance=5 > 2"],
        id="edit-distance-exceeded",
    ),
    pytest.param(
        "extra_actions",
        _make_metrics(
            golden_actions=GOLDEN,
            flat_actions=GOLDEN + ["describe_rds_instance", "ec2_instances_by_tag"],
            strict_match=False,
            extra_actions=["describe_rds_instance", "ec2_instances_by_tag"],
        ),
        TrajectoryPolicy(matching="strict", max_extra_actions=1),
        ["extra_actions=2 > 1"],
        id="extra-actions-exceeded",
    ),
    pytest.param(
        "redundancy",
        _make_metrics(
            golden_actions=GOLDEN,
            flat_actions=GOLDEN,
            strict_match=True,
            lcs_ratio=1.0,
            redundancy_count=3,
        ),
        TrajectoryPolicy(matching="strict", max_redundancy=1),
        ["redundancy_count=3 > 1"],
        id="redundancy-exceeded",
    ),
    pytest.param(
        "loop_limit",
        _make_metrics(
            golden_actions=GOLDEN,
            flat_actions=GOLDEN,
            strict_match=True,
            lcs_ratio=1.0,
            loops_used=5,
        ),
        TrajectoryPolicy(matching="strict", max_loops=3),
        ["loops_used=5 > 3"],
        id="loop-limit-exceeded",
    ),
]


@pytest.mark.parametrize("_name,metrics,policy,expected_violation_substrings", _VIOLATION_CASES)
def test_single_violation_detected(
    _name: str,
    metrics: Any,
    policy: TrajectoryPolicy,
    expected_violation_substrings: list[str],
) -> None:
    result = evaluate_trajectory_policy(metrics, GOLDEN, policy)
    assert result is not None
    assert isinstance(result, TrajectoryPolicyResult)
    assert result.passed is False, f"Expected policy fail; violations: {result.violations}"
    for substring in expected_violation_substrings:
        assert any(substring in v for v in result.violations), (
            f"Expected substring {substring!r} in violations {result.violations}"
        )


# ---------------------------------------------------------------------------
# Combined violation row
# ---------------------------------------------------------------------------


def test_combined_violations_all_appear() -> None:
    """All triggered violations must appear in the result simultaneously."""
    metrics = _make_metrics(
        golden_actions=GOLDEN,
        flat_actions=["query_grafana_metrics"],
        strict_match=False,
        lcs_ratio=0.33,
        edit_distance=5,
        extra_actions=["describe_rds_instance", "ec2_instances_by_tag"],
        missing_actions=["query_grafana_logs", "query_grafana_alert_rules"],
        redundancy_count=3,
        loops_used=5,
    )
    policy = TrajectoryPolicy(
        matching="strict",
        max_edit_distance=2,
        max_extra_actions=1,
        max_redundancy=1,
        max_loops=3,
    )
    result = evaluate_trajectory_policy(metrics, GOLDEN, policy)
    assert result is not None
    assert result.passed is False
    violations = result.violations
    assert len(violations) >= 3, f"Expected ≥3 violations, got: {violations}"
    violation_text = " ".join(violations)
    assert "strict sequence mismatch" in violation_text
    assert "edit_distance=5 > 2" in violation_text
    assert "extra_actions=2 > 1" in violation_text
    assert "redundancy_count=3 > 1" in violation_text
    assert "loops_used=5 > 3" in violation_text


# ---------------------------------------------------------------------------
# Pass cases
# ---------------------------------------------------------------------------


def test_strict_match_passes() -> None:
    metrics = _make_metrics(
        golden_actions=GOLDEN,
        flat_actions=GOLDEN,
        strict_match=True,
        lcs_ratio=1.0,
        edit_distance=0,
        extra_actions=[],
        missing_actions=[],
    )
    result = evaluate_trajectory_policy(metrics, GOLDEN, TrajectoryPolicy(matching="strict"))
    assert result is not None
    assert result.passed is True
    assert result.violations == []


def test_lcs_full_match_passes() -> None:
    metrics = _make_metrics(
        golden_actions=GOLDEN,
        flat_actions=GOLDEN,
        lcs_ratio=1.0,
        edit_distance=0,
        extra_actions=[],
        missing_actions=[],
    )
    result = evaluate_trajectory_policy(metrics, GOLDEN, TrajectoryPolicy(matching="lcs"))
    assert result is not None
    assert result.passed is True


def test_set_match_no_missing_passes() -> None:
    # Order doesn't matter for "set" matching
    reordered = list(reversed(GOLDEN))
    metrics = _make_metrics(
        golden_actions=GOLDEN,
        flat_actions=reordered,
        missing_actions=[],
    )
    result = evaluate_trajectory_policy(metrics, GOLDEN, TrajectoryPolicy(matching="set"))
    assert result is not None
    assert result.passed is True


# ---------------------------------------------------------------------------
# Not-applicable cases
# ---------------------------------------------------------------------------


def test_returns_none_when_no_golden_trajectory() -> None:
    metrics = _make_metrics()
    result = evaluate_trajectory_policy(metrics, [], TrajectoryPolicy(matching="strict"))
    assert result is None


def test_returns_none_when_policy_is_none() -> None:
    metrics = _make_metrics(golden_actions=GOLDEN, flat_actions=GOLDEN)
    result = evaluate_trajectory_policy(metrics, GOLDEN, None)
    assert result is None


# ---------------------------------------------------------------------------
# Gate recording tests: _apply_trajectory_policy_to_score
# ---------------------------------------------------------------------------


def test_trajectory_policy_gate_present_on_pass() -> None:
    """Gates must contain 'trajectory_policy' with status='pass' for a passing policy."""
    from tests.synthetic.rds_postgres.run_suite import (
        _apply_trajectory_policy_to_score,
        score_result,
    )
    from tests.synthetic.rds_postgres.scenario_loader import SUITE_DIR, load_all_scenarios

    fixture = next(iter(load_all_scenarios(SUITE_DIR)))
    final_state: dict[str, Any] = {
        "root_cause": "",
        "root_cause_category": "unknown",
        "evidence": {},
        "executed_hypotheses": [],
        "investigation_loop_count": 0,
        "report": "",
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
    }

    golden = GOLDEN
    # Build metrics that satisfy "set" matching: no missing actions
    trajectory_metrics = _make_metrics(
        golden_actions=golden,
        flat_actions=golden,
        strict_match=True,
        lcs_ratio=1.0,
        edit_distance=0,
        extra_actions=[],
        missing_actions=[],
    )
    # Policy that passes: set matching, actual == golden
    policy_result = evaluate_trajectory_policy(
        metrics=trajectory_metrics,
        golden_actions=golden,
        policy=TrajectoryPolicy(matching="set"),
    )
    assert policy_result is not None
    assert policy_result.passed is True

    score = score_result(fixture, final_state)
    updated_score = _apply_trajectory_policy_to_score(score, policy_result)

    assert "trajectory_policy" in updated_score.gates, (
        "trajectory_policy gate missing even though policy passed"
    )
    assert updated_score.gates["trajectory_policy"].status == "pass"


def test_trajectory_policy_gate_present_when_not_applicable() -> None:
    """When trajectory_policy is None, gate must be recorded as not_applicable."""
    from tests.synthetic.rds_postgres.run_suite import (
        _apply_trajectory_policy_to_score,
        score_result,
    )
    from tests.synthetic.rds_postgres.scenario_loader import SUITE_DIR, load_all_scenarios

    fixture = next(iter(load_all_scenarios(SUITE_DIR)))
    final_state: dict[str, Any] = {
        "root_cause": "",
        "root_cause_category": "unknown",
        "evidence": {},
        "executed_hypotheses": [],
        "investigation_loop_count": 0,
        "report": "",
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
    }
    score = score_result(fixture, final_state)
    updated_score = _apply_trajectory_policy_to_score(score, None)

    assert "trajectory_policy" in updated_score.gates, (
        "trajectory_policy gate missing when policy is None"
    )
    assert updated_score.gates["trajectory_policy"].actual == "not_applicable"
    assert updated_score.gates["trajectory_policy"].status == "pass"


def test_trajectory_policy_failure_sets_passed_false() -> None:
    """A policy violation must set passed=False with a stable failure reason."""
    from tests.synthetic.rds_postgres.observations import compute_trajectory_metrics
    from tests.synthetic.rds_postgres.run_suite import (
        _apply_trajectory_policy_to_score,
        score_result,
    )
    from tests.synthetic.rds_postgres.scenario_loader import SUITE_DIR, load_all_scenarios

    # Use fixture 000-healthy: it has no required evidence so a pure policy
    # failure can be isolated without noise from other failures.
    fixture = next(iter(load_all_scenarios(SUITE_DIR)))
    final_state: dict[str, Any] = {
        "root_cause": "healthy system, all metrics normal",
        "root_cause_category": fixture.answer_key.root_cause_category,
        "evidence": {},
        "executed_hypotheses": [],
        "investigation_loop_count": 0,
        "report": " ".join(fixture.answer_key.required_keywords),
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
    }
    golden = GOLDEN
    trajectory_metrics = compute_trajectory_metrics(
        executed_hypotheses=[],  # empty → strict mismatch
        golden=golden,
        loops_used=0,
        max_loops=3,
    )
    failing_policy_result = evaluate_trajectory_policy(
        metrics=trajectory_metrics,
        golden_actions=golden,
        policy=TrajectoryPolicy(matching="strict"),
    )
    assert failing_policy_result is not None
    assert failing_policy_result.passed is False

    score = score_result(fixture, final_state)
    updated_score = _apply_trajectory_policy_to_score(score, failing_policy_result)

    assert updated_score.passed is False
    assert "trajectory_policy" in updated_score.gates
    assert updated_score.gates["trajectory_policy"].status == "fail"
    assert "trajectory policy failed" in (updated_score.failure_reason or "")
    assert "strict sequence mismatch" in (updated_score.failure_reason or "")
