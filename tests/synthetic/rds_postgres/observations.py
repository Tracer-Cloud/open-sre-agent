from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Re-exported for backward compatibility — canonical definitions live in trajectory_policy.py
from tests.synthetic.rds_postgres.trajectory_policy import (
    TrajectoryMetrics,
    TrajectoryPolicy,
    TrajectoryPolicyResult,
    evaluate_trajectory_policy,
)

__all__ = [
    "TrajectoryMetrics",
    "TrajectoryPolicy",
    "TrajectoryPolicyResult",
    "evaluate_trajectory_policy",
]


@dataclass(frozen=True)
class RunObservation:
    report_schema_version: str
    scoring_formula_version: str
    scenario_id: str
    started_at: str
    wall_time_s: float
    suite: str
    backend: str
    score: dict[str, Any]
    trajectory: TrajectoryMetrics
    evaluated_golden_actions: list[str]
    trajectory_policy: TrajectoryPolicyResult | None
    trajectory_policy_version: str
    reasoning: dict[str, Any] | None
    reasoning_status: str
    observed_evidence_sources: list[str]
    required_evidence_sources: list[str]
    missing_required_evidence_sources: list[str]
    evidence_source_coverage: dict[str, Any]
    canonical_report_payload: dict[str, Any]
    final_state_digest: str
    observation_path: str = ""


def lcs_length(a: list[str], b: list[str]) -> int:
    if not a or not b:
        return 0
    rows = len(a) + 1
    cols = len(b) + 1
    dp = [[0] * cols for _ in range(rows)]
    for i in range(1, rows):
        for j in range(1, cols):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[-1][-1]


def edit_distance(a: list[str], b: list[str]) -> int:
    rows = len(a) + 1
    cols = len(b) + 1
    dp = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[-1][-1]


def final_state_digest(final_state: dict[str, Any]) -> str:
    canonical = json.dumps(final_state, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _flatten_actions(executed_hypotheses: list[dict[str, Any]]) -> tuple[list[str], list[int], int]:
    flat_actions: list[str] = []
    actions_per_loop: list[int] = []
    failed_action_count = 0

    for hypothesis in executed_hypotheses:
        actions = [str(action) for action in (hypothesis.get("actions") or [])]
        flat_actions.extend(actions)
        actions_per_loop.append(len(actions))
        failed_action_count += len(hypothesis.get("failed_actions") or [])

    return flat_actions, actions_per_loop, failed_action_count


def _duplicate_count(items: list[str]) -> int:
    counts = Counter(items)
    return sum(count - 1 for count in counts.values() if count > 1)


def _unique_in_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _source_aware_evidence_coverage(
    evidence: dict[str, Any],
    available_evidence_sources: list[str],
    required_evidence_sources: list[str],
) -> dict[str, Any]:
    available_sources = _unique_in_order(available_evidence_sources)
    required_sources = _unique_in_order(required_evidence_sources)
    candidate_sources = _unique_in_order([*available_sources, *required_sources])
    source_presence = {source: bool(evidence.get(source)) for source in candidate_sources}
    observed_sources = [source for source, present in source_presence.items() if present]

    missing_required_sources = [
        source for source in required_sources if not source_presence.get(source, False)
    ]

    required_observed = sum(1 for source in required_sources if source_presence.get(source, False))
    available_observed = sum(
        1 for source in available_sources if source_presence.get(source, False)
    )

    required_coverage = required_observed / len(required_sources) if required_sources else 1.0
    available_coverage = available_observed / len(available_sources) if available_sources else 1.0

    return {
        "available_sources": available_sources,
        "required_sources": required_sources,
        "observed_sources": observed_sources,
        "missing_required_sources": missing_required_sources,
        "source_presence": source_presence,
        "required_coverage": required_coverage,
        "available_coverage": available_coverage,
    }


def _canonical_report_payload(
    *,
    score: dict[str, Any],
    trajectory: TrajectoryMetrics,
    evaluated_golden_actions: list[str],
    trajectory_policy: TrajectoryPolicyResult | None,
    evidence_source_coverage: dict[str, Any],
) -> dict[str, Any]:
    policy_payload: dict[str, Any] | None = None
    if trajectory_policy is not None:
        policy_payload = {
            "passed": trajectory_policy.passed,
            "matching": trajectory_policy.matching,
            "violations": list(trajectory_policy.violations),
        }

    failure_reasons = score.get("failure_reasons") or []
    gates = score.get("gates") or {}
    return {
        "report_schema_version": "report_v2",
        "scoring_formula_version": "v2_gated_semantic",
        "status": "pass" if bool(score.get("passed")) else "fail",
        "category": score.get("actual_category"),
        "failure_reasons": list(failure_reasons),
        "gates": dict(gates),
        "verdict_definitions": {
            "strict_match": (
                "Strict trajectory match requires exact action order and membership equality."
            ),
            "sequencing_ok": (
                "Sequencing checks expected action coverage only; order is ignored due to parallelism."
            ),
        },
        "evidence": {
            "observed_sources": list(evidence_source_coverage["observed_sources"]),
            "required_sources": list(evidence_source_coverage["required_sources"]),
            "missing_required_sources": list(evidence_source_coverage["missing_required_sources"]),
            "source_presence": dict(evidence_source_coverage["source_presence"]),
            "required_coverage": evidence_source_coverage["required_coverage"],
            "available_coverage": evidence_source_coverage["available_coverage"],
        },
        "trajectory": {
            "golden": list(evaluated_golden_actions),
            "actual": list(trajectory.flat_actions),
            "strict_match": trajectory.strict_match,
            "lcs_ratio": trajectory.lcs_ratio,
            "edit_distance": trajectory.edit_distance,
            "coverage": trajectory.coverage,
            "extra_actions": list(trajectory.extra_actions),
            "missing_actions": list(trajectory.missing_actions),
            "redundancy_count": trajectory.redundancy_count,
            "failed_action_count": trajectory.failed_action_count,
            "policy": policy_payload,
        },
    }


def _process_metrics_summary(trajectory: TrajectoryMetrics) -> dict[str, Any]:
    """Human-readable process metrics surfaced at the top of ``score``."""
    return {
        "loops_used": trajectory.loops_used,
        "max_loops": trajectory.max_loops,
        "strict_match": trajectory.strict_match,
        "lcs_ratio": trajectory.lcs_ratio,
        "edit_distance": trajectory.edit_distance,
        "coverage": trajectory.coverage,
        "extra_actions_count": len(trajectory.extra_actions),
        "missing_actions_count": len(trajectory.missing_actions),
        "redundancy_count": trajectory.redundancy_count,
        "failed_action_count": trajectory.failed_action_count,
        "action_loops_detected": len(trajectory.actions_per_loop),
        "loop_count_consistent": trajectory.loops_used == len(trajectory.actions_per_loop),
        "definitions": {
            "extra_actions_count": (
                "Actions executed but not present in the evaluated golden trajectory."
            ),
            "missing_actions_count": (
                "Golden-trajectory actions that never appeared in execution."
            ),
            "redundancy_count": (
                "Duplicate action executions (same action run more than once). "
                "This is different from extra actions."
            ),
            "strict_match": (
                "True only when executed actions exactly match golden order and membership."
            ),
            "sequencing_ok": (
                "Coverage-only trajectory check: expected actions appear at least once; order not required."
            ),
        },
    }


def _score_with_process_metrics(
    score: dict[str, Any],
    trajectory: TrajectoryMetrics,
) -> dict[str, Any]:
    """Return score payload with process metrics first for readability."""
    return {"process_metrics": _process_metrics_summary(trajectory), **score}


def compute_trajectory_metrics(
    executed_hypotheses: list[dict[str, Any]],
    golden: list[str],
    loops_used: int,
    max_loops: int | None,
) -> TrajectoryMetrics:
    flat_actions, actions_per_loop, failed_action_count = _flatten_actions(executed_hypotheses)

    if not golden:
        return TrajectoryMetrics(
            flat_actions=flat_actions,
            actions_per_loop=actions_per_loop,
            strict_match=None,
            lcs_ratio=None,
            edit_distance=None,
            coverage=None,
            extra_actions=[],
            missing_actions=[],
            redundancy_count=_duplicate_count(flat_actions),
            loops_used=loops_used,
            max_loops=max_loops,
            loop_calibration_ok=None if max_loops is None else loops_used <= max_loops,
            failed_action_count=failed_action_count,
        )

    golden_set = set(golden)
    actual_unique = _unique_in_order(flat_actions)
    actual_set = set(actual_unique)
    missing = [action for action in golden if action not in actual_set]
    extra = [action for action in actual_unique if action not in golden_set]
    lcs = lcs_length(flat_actions, golden)

    return TrajectoryMetrics(
        flat_actions=flat_actions,
        actions_per_loop=actions_per_loop,
        strict_match=flat_actions == golden,
        lcs_ratio=lcs / len(golden),
        edit_distance=edit_distance(flat_actions, golden),
        coverage=len(golden_set & actual_set) / len(golden_set),
        extra_actions=extra,
        missing_actions=missing,
        redundancy_count=_duplicate_count(flat_actions),
        loops_used=loops_used,
        max_loops=max_loops,
        loop_calibration_ok=None if max_loops is None else loops_used <= max_loops,
        failed_action_count=failed_action_count,
    )


def build_observation(
    *,
    scenario_id: str,
    suite: str,
    backend: str,
    score: dict[str, Any],
    reasoning: dict[str, Any] | None,
    trajectory: TrajectoryMetrics,
    evaluated_golden_actions: list[str],
    trajectory_policy: TrajectoryPolicyResult | None,
    final_state: dict[str, Any],
    available_evidence_sources: list[str],
    required_evidence_sources: list[str],
    started_at: datetime,
    wall_time_s: float,
) -> RunObservation:
    evidence = final_state.get("evidence") or {}
    evidence_source_coverage = _source_aware_evidence_coverage(
        evidence=evidence,
        available_evidence_sources=available_evidence_sources,
        required_evidence_sources=required_evidence_sources,
    )
    observed_sources = list(evidence_source_coverage["observed_sources"])
    required_sources = list(evidence_source_coverage["required_sources"])
    missing_required_sources = list(evidence_source_coverage["missing_required_sources"])
    score_payload = _score_with_process_metrics(score, trajectory)

    return RunObservation(
        report_schema_version="report_v2",
        scoring_formula_version="v2_gated_semantic",
        scenario_id=scenario_id,
        started_at=started_at.astimezone(UTC).isoformat(),
        wall_time_s=round(wall_time_s, 3),
        suite=suite,
        backend=backend,
        score=score_payload,
        trajectory=trajectory,
        evaluated_golden_actions=evaluated_golden_actions,
        trajectory_policy=trajectory_policy,
        trajectory_policy_version="default_v1",
        reasoning=reasoning,
        reasoning_status="captured" if reasoning is not None else "not_captured",
        observed_evidence_sources=observed_sources,
        required_evidence_sources=required_sources,
        missing_required_evidence_sources=missing_required_sources,
        evidence_source_coverage=evidence_source_coverage,
        canonical_report_payload=_canonical_report_payload(
            score=score,
            trajectory=trajectory,
            evaluated_golden_actions=evaluated_golden_actions,
            trajectory_policy=trajectory_policy,
            evidence_source_coverage=evidence_source_coverage,
        ),
        final_state_digest=final_state_digest(final_state),
    )


def _canonical_artifact_name(canonical_report_payload: dict[str, Any], scenario_id: str) -> str:
    """Derive a 12-hex-char content-addressed filename from the canonical payload + scenario id.

    Using the canonical payload (not the full observation) means the filename is
    stable across re-runs that produce the same scoring output, making ``git diff``
    against ``_baseline/`` noise-free.
    """
    content = json.dumps(canonical_report_payload, sort_keys=True, separators=(",", ":"))
    digest = sha256((content + scenario_id).encode("utf-8")).hexdigest()[:12]
    return f"{digest}.json"


def write_observation(observation: RunObservation, observations_dir: Path) -> Path:
    scenario_dir = observations_dir / observation.scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)

    canonical_name = _canonical_artifact_name(
        observation.canonical_report_payload, observation.scenario_id
    )
    target = scenario_dir / canonical_name

    payload = _drop_none_fields(asdict(observation))
    payload["observation_path"] = str(target.relative_to(observations_dir))
    canonical_payload = dict(payload.get("canonical_report_payload") or {})
    canonical_payload["observation_path"] = payload["observation_path"]
    payload["canonical_report_payload"] = canonical_payload
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    latest = scenario_dir / "latest.json"
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def _drop_none_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none_fields(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none_fields(item) for item in value if item is not None]
    return value


def _fmt_ratio(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _fmt_list(values: list[str]) -> str:
    return "-" if not values else ", ".join(values)


def render_report_to_console(observation: RunObservation, console: Console) -> None:
    score = observation.score
    passed = bool(score.get("passed"))
    pass_label = Text("PASS" if passed else "FAIL", style="bold green" if passed else "bold red")
    status_line = Text.assemble(
        pass_label,
        f"  category={score.get('actual_category', 'unknown')}",
        f"  loops={observation.trajectory.loops_used}/{observation.trajectory.max_loops or '-'}",
        f"  wall={observation.wall_time_s:.2f}s",
    )

    correctness = Table.grid(padding=(0, 2))
    correctness.add_column(style="cyan", no_wrap=True)
    correctness.add_column()

    missing_keywords = score.get("missing_keywords") or []
    matched_keywords = score.get("matched_keywords") or []
    total_keywords = len(matched_keywords) + len(missing_keywords)
    gates = score.get("gates") or {}

    correctness.add_row("Required keywords", f"{len(matched_keywords)}/{total_keywords} matched")
    correctness.add_row(
        "Forbidden keywords",
        "clear" if (gates.get("forbidden_keyword_clear") or {}).get("status") != "fail" else "hit",
    )
    correctness.add_row(
        "Forbidden categories",
        "clear" if (gates.get("forbidden_category_clear") or {}).get("status") != "fail" else "hit",
    )
    correctness.add_row("Observed evidence", _fmt_list(observation.observed_evidence_sources))
    if observation.required_evidence_sources:
        correctness.add_row("Required evidence", _fmt_list(observation.required_evidence_sources))
        correctness.add_row(
            "Missing evidence",
            _fmt_list(observation.missing_required_evidence_sources),
        )

    trajectory = observation.trajectory
    trajectory_table = Table.grid(padding=(0, 2))
    trajectory_table.add_column(style="cyan", no_wrap=True)
    trajectory_table.add_column()

    golden = observation.evaluated_golden_actions
    trajectory_table.add_row("golden", " -> ".join(golden) if golden else "-")
    trajectory_table.add_row("actual", _fmt_list(trajectory.flat_actions))
    if trajectory.lcs_ratio is not None:
        match_text = (
            f"strict={trajectory.strict_match} "
            f"(lcs={_fmt_ratio(trajectory.lcs_ratio)}, edit_distance={trajectory.edit_distance})"
        )
        trajectory_table.add_row("match", match_text)
    trajectory_table.add_row("extras", _fmt_list(trajectory.extra_actions))
    trajectory_table.add_row("missing", _fmt_list(trajectory.missing_actions))
    trajectory_table.add_row("redundant", str(trajectory.redundancy_count))
    trajectory_table.add_row("per-loop", str(trajectory.actions_per_loop))
    trajectory_table.add_row("failed", str(trajectory.failed_action_count))
    if observation.trajectory_policy is not None:
        policy = observation.trajectory_policy
        policy_status = "pass" if policy.passed else "fail"
        trajectory_table.add_row("policy", f"{policy_status} ({policy.matching})")
        if policy.violations:
            trajectory_table.add_row("violations", "; ".join(policy.violations))

    body = Group(
        status_line,
        Text(""),
        Text("Correctness", style="bold cyan"),
        correctness,
        Text(""),
        Text("Trajectory", style="bold cyan"),
        trajectory_table,
        Text(""),
        Text(f"Observation: {observation.observation_path or '(not persisted)'}", style="dim"),
    )
    console.print(
        Panel(
            body,
            title=f"Synthetic RDS Run - {observation.scenario_id}",
            border_style="green" if passed else "red",
        )
    )


def render_report_to_string(observation: RunObservation) -> str:
    console = Console(record=True, width=120, color_system=None, highlight=False)
    render_report_to_console(observation, console)
    return console.export_text()
