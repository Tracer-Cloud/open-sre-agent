from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from tests.synthetic.schemas import (
    GoldenTrajectorySchema,
    ScenarioEvidence,
    ScenarioMetadataSchema,
    validate_alert,
    validate_answer_key,
    validate_cloudwatch_metrics,
    validate_ec2_instances_by_tag,
    validate_elb_target_health,
    validate_performance_insights,
    validate_rds_events,
    validate_scenario_metadata,
)

SUITE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ScenarioMetadata:
    schema_version: str
    scenario_id: str
    engine: str
    engine_version: str
    instance_class: str
    region: str
    db_instance_identifier: str
    db_cluster: str
    failure_mode: str
    severity: str
    available_evidence: list[str]
    scenario_difficulty: int = 1
    adversarial_signals: list[str] = ()  # type: ignore[assignment]
    depends_on: str = ""


TrajectoryMatching = Literal["strict", "lcs", "set"]


@dataclass(frozen=True)
class GoldenTrajectoryConfig:
    ordered_actions: list[str]
    matching: TrajectoryMatching = "lcs"
    max_edit_distance: int | None = None
    max_extra_actions: int | None = None
    max_redundancy: int | None = None
    max_loops: int | None = None


@dataclass(frozen=True)
class ScenarioAnswerKey:
    root_cause_category: str
    required_keywords: list[str]
    model_response: str
    forbidden_categories: list[str] = ()  # type: ignore[assignment]
    forbidden_keywords: list[str] = ()  # type: ignore[assignment]
    required_evidence_sources: list[str] = ()  # type: ignore[assignment]
    optimal_trajectory: list[str] = ()  # type: ignore[assignment]
    max_investigation_loops: int = 1
    ruling_out_keywords: list[str] = ()  # type: ignore[assignment]
    required_queries: list[str] = ()  # type: ignore[assignment]
    golden_trajectory: GoldenTrajectoryConfig | None = None


@dataclass(frozen=True)
class ScenarioFixture:
    scenario_id: str
    scenario_dir: Path
    alert: dict[str, Any]
    evidence: ScenarioEvidence
    metadata: ScenarioMetadata
    answer_key: ScenarioAnswerKey
    problem_md: str


def _parse_trajectory_matching(value: Any) -> TrajectoryMatching:
    if value in {"strict", "lcs", "set"}:
        return cast(TrajectoryMatching, value)
    raise ValueError(
        "answer.yml: 'golden_trajectory.matching' must be one of "
        "'strict', 'lcs', or 'set' when present"
    )


def _parse_non_negative_int(
    golden_trajectory: GoldenTrajectorySchema | dict[str, Any], field: str
) -> int | None:
    value = golden_trajectory.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"answer.yml: 'golden_trajectory.{field}' must be a non-negative integer when present"
        )
    return cast(int, value)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected YAML object in {path}")
    return payload


# ---------------------------------------------------------------------------
# Base-inheritance helpers
# ---------------------------------------------------------------------------


def _resolve_base_dir(suite_dir: Path, base_id: str) -> Path:
    """Find the base scenario directory by its directory name (e.g. '000-healthy')."""
    base_dir = suite_dir / base_id
    if not base_dir.is_dir():
        raise ValueError(f"Base scenario '{base_id}' not found at {base_dir}")
    base_raw = _read_yaml(base_dir / "scenario.yml")
    if "base" in base_raw:
        raise ValueError(
            f"Chained inheritance is not supported: base scenario '{base_id}' "
            f"itself declares base '{base_raw['base']}'"
        )
    return base_dir


def _merge_scenario_yaml(
    base_raw: dict[str, Any],
    scenario_raw: dict[str, Any],
) -> dict[str, Any]:
    """Shallow-merge scenario overrides on top of base metadata.

    scenario_raw values win. The ``base`` directive is consumed and removed.
    """
    merged = {**base_raw, **{k: v for k, v in scenario_raw.items() if k != "base"}}
    merged.pop("base", None)
    return merged


def _resolve_evidence_path(
    scenario_dir: Path,
    base_dir: Path | None,
    filename: str,
) -> Path:
    """Return the scenario's own evidence file if it exists, otherwise the base's."""
    for search_dir in (scenario_dir, base_dir):
        if search_dir is None:
            continue
        candidate = search_dir / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Evidence '{filename}' not found in {scenario_dir}"
        + (f" or base {base_dir}" if base_dir else "")
    )


def _has_split_cloudwatch_metrics(scenario_dir: Path) -> bool:
    """Check whether a directory uses per-metric prefixed files."""
    return (scenario_dir / "aws_cloudwatch_metrics_envelope.json").exists()


def _load_cloudwatch_metrics_split(scenario_dir: Path) -> dict[str, Any]:
    """Assemble CloudWatch metrics from prefixed per-metric files.

    Expects ``aws_cloudwatch_metrics_envelope.json`` (shared metadata) and
    ``aws_cloudwatch_metrics_<MetricName>.json`` files in *scenario_dir*.
    """
    envelope = _read_json(scenario_dir / "aws_cloudwatch_metrics_envelope.json")
    prefix = "aws_cloudwatch_metrics_"
    metrics = []
    for f in sorted(scenario_dir.glob(f"{prefix}*.json")):
        if f.name == f"{prefix}envelope.json":
            continue
        metrics.append(_read_json(f))
    envelope["metric_data_results"] = metrics
    return envelope


def _load_cloudwatch_metrics(
    scenario_dir: Path,
    base_dir: Path | None,
) -> dict[str, Any]:
    """Load CloudWatch metrics — consolidated file or per-metric split."""
    # 1. Scenario has a consolidated file
    single = scenario_dir / "aws_cloudwatch_metrics.json"
    if single.is_file():
        return _read_json(single)
    # 2. Scenario has per-metric split files
    if _has_split_cloudwatch_metrics(scenario_dir):
        return _load_cloudwatch_metrics_split(scenario_dir)
    # 3. Fall back to base
    if base_dir is not None:
        base_single = base_dir / "aws_cloudwatch_metrics.json"
        if base_single.is_file():
            return _read_json(base_single)
        if _has_split_cloudwatch_metrics(base_dir):
            return _load_cloudwatch_metrics_split(base_dir)
    raise FileNotFoundError(
        f"CloudWatch metrics not found in {scenario_dir}"
        + (f" or base {base_dir}" if base_dir else "")
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _validated_metadata(raw: dict[str, Any]) -> ScenarioMetadata:
    """Validate a (possibly merged) raw dict and return a ScenarioMetadata."""
    validated: ScenarioMetadataSchema = validate_scenario_metadata(raw)
    return ScenarioMetadata(
        schema_version=validated["schema_version"],
        scenario_id=validated["scenario_id"],
        engine=validated["engine"],
        engine_version=validated["engine_version"],
        instance_class=validated["instance_class"],
        region=validated["region"],
        db_instance_identifier=validated["db_instance_identifier"],
        db_cluster=validated.get("db_cluster", ""),
        failure_mode=validated["failure_mode"],
        severity=validated["severity"],
        available_evidence=list(validated["available_evidence"]),
        scenario_difficulty=validated.get("scenario_difficulty", 1),  # type: ignore[arg-type]
        adversarial_signals=list(validated.get("adversarial_signals") or []),
        depends_on=validated.get("depends_on", ""),  # type: ignore[arg-type]
    )


def _parse_scenario_yaml(path: Path) -> tuple[ScenarioMetadata, Path | None]:
    """Parse scenario.yml, resolving base inheritance if declared.

    Returns (metadata, base_dir) where base_dir is the resolved base scenario
    directory, or None if no ``base`` field was declared.
    """
    raw = _read_yaml(path)
    base_id = raw.get("base")
    base_dir: Path | None = None

    if base_id:
        suite_dir = path.parent.parent
        base_dir = _resolve_base_dir(suite_dir, base_id)
        base_raw = _read_yaml(base_dir / "scenario.yml")
        raw = _merge_scenario_yaml(base_raw, raw)

    return _validated_metadata(raw), base_dir


def _parse_answer_yaml(path: Path) -> ScenarioAnswerKey:
    payload = _read_yaml(path)
    validated = validate_answer_key(payload)
    golden_trajectory_raw = validated.get("golden_trajectory")
    golden_trajectory: GoldenTrajectoryConfig | None = None
    if isinstance(golden_trajectory_raw, dict):
        ordered_actions_raw = golden_trajectory_raw.get("ordered_actions")
        if not isinstance(ordered_actions_raw, list) or not ordered_actions_raw:
            raise ValueError(
                "answer.yml: 'golden_trajectory.ordered_actions' must be a non-empty list "
                "of strings when present"
            )
        ordered_actions = [action.strip() for action in ordered_actions_raw]
        matching = _parse_trajectory_matching(golden_trajectory_raw.get("matching", "lcs"))
        golden_trajectory = GoldenTrajectoryConfig(
            ordered_actions=ordered_actions,
            matching=matching,
            max_edit_distance=_parse_non_negative_int(golden_trajectory_raw, "max_edit_distance"),
            max_extra_actions=_parse_non_negative_int(golden_trajectory_raw, "max_extra_actions"),
            max_redundancy=_parse_non_negative_int(golden_trajectory_raw, "max_redundancy"),
            max_loops=_parse_non_negative_int(golden_trajectory_raw, "max_loops"),
        )

    return ScenarioAnswerKey(
        root_cause_category=validated["root_cause_category"].strip(),
        required_keywords=[k.strip() for k in validated["required_keywords"]],
        model_response=validated["model_response"].strip(),
        forbidden_categories=list(validated.get("forbidden_categories") or []),
        forbidden_keywords=list(validated.get("forbidden_keywords") or []),
        required_evidence_sources=list(validated.get("required_evidence_sources") or []),
        optimal_trajectory=list(validated.get("optimal_trajectory") or []),
        max_investigation_loops=int(validated.get("max_investigation_loops") or 1),
        ruling_out_keywords=list(validated.get("ruling_out_keywords") or []),
        required_queries=list(validated.get("required_queries") or []),
        golden_trajectory=golden_trajectory,
    )


def _build_problem_md(alert: dict[str, Any], metadata: ScenarioMetadata) -> str:
    title = str(alert.get("title") or metadata.scenario_id)
    annotations = alert.get("commonAnnotations", {}) or {}

    parts = [
        f"# {title}",
        (
            f"Service: RDS {metadata.engine.upper()}"
            f" | Severity: {metadata.severity}"
            f" | Scenario: {metadata.failure_mode}"
        ),
        f"Scenario ID: {metadata.scenario_id}",
        f"DB instance: {metadata.db_instance_identifier}",
    ]

    if metadata.db_cluster:
        parts.append(f"DB cluster: {metadata.db_cluster}")

    summary = annotations.get("summary")
    if summary:
        parts.append(f"\nSummary: {summary}")

    error = annotations.get("error")
    if error and error != summary:
        parts.append(f"\nError: {error}")

    suspected = annotations.get("suspected_symptom")
    if suspected:
        parts.append(f"\nObserved symptom: {suspected}")

    return "\n".join(parts)


def _build_evidence(
    scenario_dir: Path,
    available_evidence: list[str],
    base_dir: Path | None = None,
) -> ScenarioEvidence:
    """Load only the evidence sources declared in scenario.yml:available_evidence.

    When *base_dir* is set, evidence files missing from *scenario_dir* are
    resolved from the base scenario directory (file-level fallback).
    """
    aws_cloudwatch_metrics = None
    aws_rds_events = None
    aws_performance_insights = None
    ec2_instances_by_tag = None
    elb_target_health = None

    if "aws_cloudwatch_metrics" in available_evidence:
        raw = _load_cloudwatch_metrics(scenario_dir, base_dir)
        aws_cloudwatch_metrics = validate_cloudwatch_metrics(raw)

    if "aws_rds_events" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "aws_rds_events.json")
        raw_events = validate_rds_events(_read_json(path))
        aws_rds_events = raw_events.get("events", [])

    if "aws_performance_insights" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "aws_performance_insights.json")
        aws_performance_insights = validate_performance_insights(_read_json(path))

    if "ec2_instances_by_tag" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "ec2_instances_by_tag.json")
        ec2_instances_by_tag = validate_ec2_instances_by_tag(_read_json(path))

    if "elb_target_health" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "elb_target_health.json")
        elb_target_health = validate_elb_target_health(_read_json(path))

    return ScenarioEvidence(
        aws_cloudwatch_metrics=aws_cloudwatch_metrics,
        aws_rds_events=aws_rds_events,
        aws_performance_insights=aws_performance_insights,
        ec2_instances_by_tag=ec2_instances_by_tag,
        elb_target_health=elb_target_health,
    )


def load_scenario(scenario_dir: Path) -> ScenarioFixture:
    metadata, base_dir = _parse_scenario_yaml(scenario_dir / "scenario.yml")

    alert_path = _resolve_evidence_path(scenario_dir, base_dir, "alert.json")
    alert = cast(dict[str, Any], validate_alert(_read_json(alert_path)))

    evidence = _build_evidence(scenario_dir, metadata.available_evidence, base_dir)
    answer_key = _parse_answer_yaml(scenario_dir / "answer.yml")
    problem_md = _build_problem_md(alert, metadata)

    return ScenarioFixture(
        scenario_id=scenario_dir.name,
        scenario_dir=scenario_dir,
        alert=alert,
        evidence=evidence,
        metadata=metadata,
        answer_key=answer_key,
        problem_md=problem_md,
    )


def load_all_scenarios(root_dir: Path | None = None) -> list[ScenarioFixture]:
    base_dir = root_dir or SUITE_DIR
    scenario_dirs = sorted(
        path for path in base_dir.iterdir() if path.is_dir() and path.name[:3].isdigit()
    )
    return [load_scenario(path) for path in scenario_dirs]
