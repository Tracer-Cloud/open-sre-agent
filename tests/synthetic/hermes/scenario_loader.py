"""Load Hermes synthetic scenarios from disk into typed fixtures.

A scenario directory must contain ``scenario.yml``, ``answer.yml``, and a
log fixture (``errors.log`` by default). Numeric prefixes (``000-...``)
order scenarios for deterministic parametrization in pytest.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.hermes.classifier import (
    DEFAULT_TRACEBACK_FOLLOWUP_S,
    DEFAULT_WARNING_BURST_THRESHOLD,
    DEFAULT_WARNING_BURST_WINDOW_S,
)

SUITE_DIR = Path(__file__).resolve().parent

_SCENARIO_PREFIX_RE = re.compile(r"^\d{3}-")
_COUNT_RE = re.compile(r"^\s*(==|>=|<=|>|<)\s*(\d+)\s*$")
_COUNT_OPS: dict[str, Callable[[int, int], bool]] = {
    "==": operator.eq,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


@dataclass(frozen=True, slots=True)
class ClassifierConfig:
    warning_burst_threshold: int = DEFAULT_WARNING_BURST_THRESHOLD
    warning_burst_window_s: float = DEFAULT_WARNING_BURST_WINDOW_S
    traceback_followup_s: float = DEFAULT_TRACEBACK_FOLLOWUP_S


@dataclass(frozen=True, slots=True)
class ExpectedIncident:
    """Partial-match constraints for a single emitted incident."""

    rule: str
    severity: str | None = None
    logger: str | None = None
    title_contains: str | None = None
    min_records: int = 1
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class IncidentCountAssertion:
    """``expected_incident_count`` entry: rule -> (op, value)."""

    op: str
    value: int

    def matches(self, observed: int) -> bool:
        return _COUNT_OPS[self.op](observed, self.value)


@dataclass(frozen=True, slots=True)
class HermesScenarioMetadata:
    scenario_id: str
    title: str
    source: str
    log_file: str
    classifier: ClassifierConfig


@dataclass(frozen=True, slots=True)
class HermesAnswerKey:
    expected_incidents: tuple[ExpectedIncident, ...]
    expected_incident_count: dict[str, IncidentCountAssertion]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class HermesScenarioFixture:
    scenario_id: str
    scenario_dir: Path
    metadata: HermesScenarioMetadata
    answer_key: HermesAnswerKey
    log_path: Path
    log_lines: tuple[str, ...] = field(default_factory=tuple)


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return payload


def _parse_classifier_config(raw: dict[str, Any] | None) -> ClassifierConfig:
    if not raw:
        return ClassifierConfig()
    return ClassifierConfig(
        warning_burst_threshold=int(
            raw.get("warning_burst_threshold", DEFAULT_WARNING_BURST_THRESHOLD)
        ),
        warning_burst_window_s=float(
            raw.get("warning_burst_window_s", DEFAULT_WARNING_BURST_WINDOW_S)
        ),
        traceback_followup_s=float(raw.get("traceback_followup_s", DEFAULT_TRACEBACK_FOLLOWUP_S)),
    )


def _parse_metadata(scenario_dir: Path) -> HermesScenarioMetadata:
    raw = _read_yaml(scenario_dir / "scenario.yml")
    scenario_id = str(raw.get("scenario_id") or scenario_dir.name)
    title = str(raw.get("title") or scenario_id)
    source = str(raw.get("source") or "unknown")
    log_file = str(raw.get("log_file") or "errors.log")
    classifier = _parse_classifier_config(raw.get("classifier"))
    return HermesScenarioMetadata(
        scenario_id=scenario_id,
        title=title,
        source=source,
        log_file=log_file,
        classifier=classifier,
    )


def _parse_expected_incident(raw: dict[str, Any]) -> ExpectedIncident:
    rule = raw.get("rule")
    if not isinstance(rule, str) or not rule:
        raise ValueError("expected incident must declare a non-empty 'rule'")
    return ExpectedIncident(
        rule=rule,
        severity=_optional_str(raw.get("severity")),
        logger=_optional_str(raw.get("logger")),
        title_contains=_optional_str(raw.get("title_contains")),
        min_records=int(raw.get("min_records", 1)),
        run_id=_optional_str(raw.get("run_id")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_count_assertions(raw: dict[str, Any] | None) -> dict[str, IncidentCountAssertion]:
    if not raw:
        return {}
    parsed: dict[str, IncidentCountAssertion] = {}
    for rule, expression in raw.items():
        match = _COUNT_RE.match(str(expression))
        if match is None:
            raise ValueError(
                f"invalid count expression for rule {rule!r}: {expression!r} "
                "(expected one of ==N, >=N, <=N, >N, <N)"
            )
        parsed[str(rule)] = IncidentCountAssertion(op=match.group(1), value=int(match.group(2)))
    return parsed


def _parse_answer_key(scenario_dir: Path) -> HermesAnswerKey:
    raw = _read_yaml(scenario_dir / "answer.yml")
    raw_incidents = raw.get("expected_incidents") or []
    if not isinstance(raw_incidents, list):
        raise ValueError("expected_incidents must be a list of mappings")
    expected = tuple(_parse_expected_incident(item) for item in raw_incidents)
    count_assertions = _parse_count_assertions(raw.get("expected_incident_count"))
    notes = str(raw.get("notes") or "").strip()
    return HermesAnswerKey(
        expected_incidents=expected,
        expected_incident_count=count_assertions,
        notes=notes,
    )


def load_scenario(scenario_dir: Path) -> HermesScenarioFixture:
    metadata = _parse_metadata(scenario_dir)
    answer_key = _parse_answer_key(scenario_dir)

    log_path = scenario_dir / metadata.log_file
    if not log_path.exists():
        raise FileNotFoundError(f"log fixture not found: {log_path}")

    log_lines = tuple(log_path.read_text(encoding="utf-8").splitlines())

    return HermesScenarioFixture(
        scenario_id=metadata.scenario_id,
        scenario_dir=scenario_dir,
        metadata=metadata,
        answer_key=answer_key,
        log_path=log_path,
        log_lines=log_lines,
    )


def load_all_scenarios(root_dir: Path | None = None) -> list[HermesScenarioFixture]:
    base_dir = root_dir or SUITE_DIR
    scenario_dirs = sorted(
        path
        for path in base_dir.iterdir()
        if path.is_dir() and _SCENARIO_PREFIX_RE.match(path.name)
    )
    return [load_scenario(path) for path in scenario_dirs]


__all__ = [
    "ClassifierConfig",
    "ExpectedIncident",
    "HermesAnswerKey",
    "HermesScenarioFixture",
    "HermesScenarioMetadata",
    "IncidentCountAssertion",
    "SUITE_DIR",
    "load_all_scenarios",
    "load_scenario",
]
