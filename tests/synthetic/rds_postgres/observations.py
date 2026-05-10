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


@dataclass(frozen=True)
class TrajectoryMetrics:
    flat_actions: list[str]
    actions_per_loop: list[int]
    strict_match: bool | None
    lcs_ratio: float | None
    edit_distance: int | None
    coverage: float | None
    extra_actions: list[str]
    missing_actions: list[str]
    redundancy_count: int
    loops_used: int
    max_loops: int | None
    loop_calibration_ok: bool | None
    failed_action_count: int


@dataclass(frozen=True)
class RunObservation:
    scenario_id: str
    started_at: str
    wall_time_s: float
    suite: str
    backend: str
    score: dict[str, Any]
    trajectory: TrajectoryMetrics
    reasoning: dict[str, Any] | None
    evidence_keys_present: list[str]
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
    final_state: dict[str, Any],
    started_at: datetime,
    wall_time_s: float,
) -> RunObservation:
    evidence = final_state.get("evidence") or {}
    evidence_keys = sorted(str(key) for key in evidence if evidence.get(key))

    return RunObservation(
        scenario_id=scenario_id,
        started_at=started_at.astimezone(UTC).isoformat(),
        wall_time_s=round(wall_time_s, 3),
        suite=suite,
        backend=backend,
        score=score,
        trajectory=trajectory,
        reasoning=reasoning,
        evidence_keys_present=evidence_keys,
        final_state_digest=final_state_digest(final_state),
    )


def write_observation(observation: RunObservation, observations_dir: Path) -> Path:
    scenario_dir = observations_dir / observation.scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)

    status = "pass" if bool(observation.score.get("passed")) else "fail"
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    target = scenario_dir / f"{stamp}__{status}.json"

    payload = asdict(observation)
    payload["observation_path"] = str(target.relative_to(observations_dir))
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    latest = scenario_dir / "latest.json"
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


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

    correctness.add_row("Required keywords", f"{len(matched_keywords)}/{total_keywords} matched")
    correctness.add_row(
        "Forbidden keywords",
        "clear" if not score.get("failure_reason", "").startswith("forbidden keywords") else "hit",
    )
    correctness.add_row(
        "Forbidden categories",
        "clear" if not score.get("failure_reason", "").startswith("forbidden category") else "hit",
    )
    correctness.add_row("Evidence sources", _fmt_list(observation.evidence_keys_present))

    trajectory = observation.trajectory
    trajectory_table = Table.grid(padding=(0, 2))
    trajectory_table.add_column(style="cyan", no_wrap=True)
    trajectory_table.add_column()

    score_trajectory = score.get("trajectory") or {}
    golden = score_trajectory.get("expected_sequence") or []
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
