from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.io import REALRCA_DMA, load_json, rows_by_case

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class AnswerKey:
    """Stable key for one non-reference answer variant in one case."""

    case_id: str
    fingerprint: str

    @property
    def case_suffix(self) -> str:
        return self.case_id.rsplit("-", 1)[-1][-4:]

    def to_dict(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "case_suffix": self.case_suffix,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class SubmissionConstraint:
    """One submitted result file represented as a score delta constraint."""

    agent_name: str
    result_path: str
    accuracy: float
    delta: float
    changed_answers: list[AnswerKey]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "result_path": self.result_path,
            "accuracy": self.accuracy,
            "delta": self.delta,
            "changed_answers": [item.to_dict() for item in self.changed_answers],
        }


@dataclass(frozen=True)
class AnswerDeltaEstimate:
    """Estimated score contribution for one answer variant versus the current reference."""

    case_id: str
    case_suffix: str
    fingerprint: str
    estimate: float
    min_estimate: float
    max_estimate: float
    observation_count: int
    methods: list[str]
    agents: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseTomography:
    """All inferred answer deltas for one case suffix."""

    case_id: str
    case_suffix: str
    best_estimate: float | None
    best_fingerprint: str | None
    estimates: list[AnswerDeltaEstimate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_suffix": self.case_suffix,
            "best_estimate": self.best_estimate,
            "best_fingerprint": self.best_fingerprint,
            "estimates": [item.to_dict() for item in self.estimates],
        }


@dataclass(frozen=True)
class TomographyReport:
    """Public-feedback tomography over local RealRCA submissions."""

    team_name: str
    reference_result_path: str
    reference_accuracy: float
    matched_submission_count: int
    constraint_count: int
    inferred_answer_count: int
    positive_answer_count: int
    constraints: list[SubmissionConstraint]
    cases: list[CaseTomography]

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_name": self.team_name,
            "reference_result_path": self.reference_result_path,
            "reference_accuracy": self.reference_accuracy,
            "matched_submission_count": self.matched_submission_count,
            "constraint_count": self.constraint_count,
            "inferred_answer_count": self.inferred_answer_count,
            "positive_answer_count": self.positive_answer_count,
            "constraints": [item.to_dict() for item in self.constraints],
            "cases": [item.to_dict() for item in self.cases],
        }


def build_tomography_report(
    *,
    leaderboard_path: Path,
    reference_result_path: Path,
    results_dir: Path | Sequence[Path] = REALRCA_DMA,
    team_name: str = "隐元玩一玩",
    reference_agent_name: str | None = None,
    max_passes: int = 8,
) -> TomographyReport:
    """Infer candidate answer deltas from public aggregate scores and local submissions."""

    leaderboard = load_json(leaderboard_path)
    accuracy_by_agent = _accuracy_by_agent(leaderboard, team_name=team_name)
    reference_accuracy = _reference_accuracy(
        accuracy_by_agent,
        reference_agent_name=reference_agent_name,
    )
    reference_rows = rows_by_case(reference_result_path)
    constraints = _submission_constraints(
        results_dirs=_result_dirs(results_dir),
        accuracy_by_agent=accuracy_by_agent,
        reference_rows=reference_rows,
        reference_accuracy=reference_accuracy,
    )
    estimates = _infer_answer_deltas(constraints, max_passes=max_passes)
    cases = _case_reports(estimates)
    return TomographyReport(
        team_name=team_name,
        reference_result_path=str(reference_result_path),
        reference_accuracy=reference_accuracy,
        matched_submission_count=len(constraints),
        constraint_count=sum(1 for item in constraints if item.changed_answers),
        inferred_answer_count=len(estimates),
        positive_answer_count=sum(1 for item in estimates if item.estimate > 0.05),
        constraints=constraints,
        cases=cases,
    )


def render_tomography_markdown(report: TomographyReport, *, limit: int = 40) -> str:
    """Render a compact tomography report for probe planning."""

    lines = [
        "# RealRCA Score Tomography",
        "",
        f"- team: `{report.team_name}`",
        f"- reference: `{report.reference_result_path}`",
        f"- reference_accuracy: `{report.reference_accuracy}`",
        f"- matched_submissions: `{report.matched_submission_count}`",
        f"- constraints: `{report.constraint_count}`",
        f"- inferred_answers: `{report.inferred_answer_count}`",
        f"- positive_answers: `{report.positive_answer_count}`",
        "",
        "| rank | case | best delta | observations | methods | agents |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    ranked = [item for item in report.cases if item.best_estimate is not None]
    ranked.sort(key=lambda item: (-(item.best_estimate or 0.0), item.case_suffix))
    for index, case in enumerate(ranked[:limit], start=1):
        best = case.estimates[0] if case.estimates else None
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{case.case_suffix}`",
                    f"{case.best_estimate or 0.0:.2f}",
                    str(best.observation_count if best else 0),
                    ",".join(best.methods if best else []),
                    ", ".join((best.agents if best else [])[:3]),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _accuracy_by_agent(payload: Any, *, team_name: str) -> dict[str, float]:
    items = payload.get("items", []) if isinstance(payload, dict) else []
    output: dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("team_name") != team_name:
            continue
        agent_name = item.get("agent_name")
        if not isinstance(agent_name, str) or not agent_name:
            continue
        try:
            output[agent_name] = float(item.get("accuracy"))
        except (TypeError, ValueError):
            continue
    return output


def _reference_accuracy(
    accuracy_by_agent: dict[str, float],
    *,
    reference_agent_name: str | None,
) -> float:
    if reference_agent_name:
        try:
            return accuracy_by_agent[reference_agent_name]
        except KeyError as exc:
            raise KeyError(
                f"reference agent {reference_agent_name!r} not found in leaderboard"
            ) from exc
    if not accuracy_by_agent:
        raise ValueError("no matching leaderboard rows with numeric accuracy")
    return max(accuracy_by_agent.values())


def _submission_constraints(
    *,
    results_dirs: Sequence[Path],
    accuracy_by_agent: dict[str, float],
    reference_rows: dict[str, Any],
    reference_accuracy: float,
) -> list[SubmissionConstraint]:
    constraints: list[SubmissionConstraint] = []
    seen_submission_paths: set[Path] = set()
    for results_dir in results_dirs:
        for submission_path in sorted(results_dir.glob("submission-test-*.json")):
            resolved_submission_path = submission_path.resolve()
            if resolved_submission_path in seen_submission_paths:
                continue
            seen_submission_paths.add(resolved_submission_path)
            result_path = (
                results_dir
                / f"results-test-{submission_path.stem.removeprefix('submission-test-')}.json"
            )
            if not result_path.exists():
                continue
            try:
                submission = load_json(submission_path)
                result_rows = rows_by_case(result_path)
            except (OSError, ValueError):
                continue
            agent_name = _submission_agent_name(submission)
            if agent_name not in accuracy_by_agent:
                continue
            if set(result_rows) != set(reference_rows):
                continue
            changed = [
                AnswerKey(case_id=case_id, fingerprint=_answer_fingerprint(answer))
                for case_id, answer in result_rows.items()
                if _answer_fingerprint(answer) != _answer_fingerprint(reference_rows[case_id])
            ]
            constraints.append(
                SubmissionConstraint(
                    agent_name=agent_name,
                    result_path=str(result_path),
                    accuracy=accuracy_by_agent[agent_name],
                    delta=round(accuracy_by_agent[agent_name] - reference_accuracy, 4),
                    changed_answers=changed,
                )
            )
    return constraints


def _result_dirs(results_dir: Path | Sequence[Path]) -> list[Path]:
    if isinstance(results_dir, Path):
        return [results_dir]
    return list(results_dir)


def _submission_agent_name(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    response = payload.get("submission_response")
    if isinstance(response, dict):
        submission = response.get("submission")
        if isinstance(submission, dict) and isinstance(submission.get("agent_name"), str):
            return submission["agent_name"]
    credential = payload.get("credential")
    if isinstance(credential, dict) and isinstance(credential.get("agent_name"), str):
        return credential["agent_name"]
    return ""


def _answer_fingerprint(answer: Any) -> str:
    trace_id = getattr(answer, "trace_id", "")
    diagnosis = getattr(answer, "diagnosis_output", "")
    normalized = _WHITESPACE_RE.sub(" ", str(diagnosis).strip())
    payload = f"{str(trace_id).strip()}\n{normalized}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _infer_answer_deltas(
    constraints: Sequence[SubmissionConstraint],
    *,
    max_passes: int,
) -> list[AnswerDeltaEstimate]:
    observations: dict[AnswerKey, list[tuple[float, str, str]]] = defaultdict(list)
    seen: set[tuple[AnswerKey, str, str]] = set()

    def add(key: AnswerKey, value: float, method: str, agent_name: str) -> bool:
        rounded = round(value, 4)
        marker = (key, method, agent_name)
        if marker in seen:
            return False
        seen.add(marker)
        observations[key].append((rounded, method, agent_name))
        return True

    for constraint in constraints:
        if len(constraint.changed_answers) == 1:
            add(
                constraint.changed_answers[0],
                constraint.delta,
                "direct_single_case",
                constraint.agent_name,
            )

    for _pass in range(max_passes):
        changed = False
        known_values = {
            key: sum(value for value, _method, _agent in values) / len(values)
            for key, values in observations.items()
            if values
        }
        for constraint in constraints:
            unknown = [key for key in constraint.changed_answers if key not in known_values]
            if len(unknown) != 1:
                continue
            known_sum = sum(
                known_values[key] for key in constraint.changed_answers if key in known_values
            )
            changed |= add(
                unknown[0],
                constraint.delta - known_sum,
                "constraint_single_unknown",
                constraint.agent_name,
            )
        if not changed:
            break

    return [_estimate_from_observations(key, values) for key, values in observations.items()]


def _estimate_from_observations(
    key: AnswerKey,
    values: Sequence[tuple[float, str, str]],
) -> AnswerDeltaEstimate:
    estimates = [value for value, _method, _agent in values]
    methods = sorted({method for _value, method, _agent in values})
    agents = sorted({agent for _value, _method, agent in values})
    average = sum(estimates) / len(estimates)
    return AnswerDeltaEstimate(
        case_id=key.case_id,
        case_suffix=key.case_suffix,
        fingerprint=key.fingerprint,
        estimate=round(average, 4),
        min_estimate=round(min(estimates), 4),
        max_estimate=round(max(estimates), 4),
        observation_count=len(estimates),
        methods=methods,
        agents=agents,
    )


def _case_reports(estimates: Iterable[AnswerDeltaEstimate]) -> list[CaseTomography]:
    grouped: dict[str, list[AnswerDeltaEstimate]] = defaultdict(list)
    for estimate in estimates:
        grouped[estimate.case_id].append(estimate)
    cases: list[CaseTomography] = []
    for case_id, case_estimates in grouped.items():
        ranked = sorted(
            case_estimates,
            key=lambda item: (-item.estimate, -item.observation_count, item.fingerprint),
        )
        cases.append(
            CaseTomography(
                case_id=case_id,
                case_suffix=case_id.rsplit("-", 1)[-1][-4:],
                best_estimate=ranked[0].estimate if ranked else None,
                best_fingerprint=ranked[0].fingerprint if ranked else None,
                estimates=ranked,
            )
        )
    cases.sort(key=lambda item: (-(item.best_estimate or 0.0), item.case_suffix))
    return cases
