from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tests.synthetic.hermes_rca.hermes_schemas import (
    HermesScenarioAnswerKeySchema,
    HermesScenarioEvidence,
    HermesScenarioMetadataSchema,
    validate_hermes_adapter_catalog,
    validate_hermes_alert,
    validate_hermes_answer_key,
    validate_hermes_approval_events,
    validate_hermes_audit_trail,
    validate_hermes_config,
    validate_hermes_credential_state,
    validate_hermes_cron_state,
    validate_hermes_filesystem_state,
    validate_hermes_kv_cache_state,
    validate_hermes_memory_state,
    validate_hermes_message_history,
    validate_hermes_orchestration_state,
    validate_hermes_provider_traffic,
    validate_hermes_rbac_state,
    validate_hermes_routing_decisions,
    validate_hermes_runtime_state,
    validate_hermes_scenario_metadata,
    validate_hermes_session_log,
    validate_hermes_session_topology,
    validate_hermes_workflow_run,
)

SUITE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class HermesScenarioMetadata:
    schema_version: str
    scenario_id: str
    failure_mode: str
    severity: str
    available_evidence: list[str]
    scenario_difficulty: int = 1


@dataclass(frozen=True)
class HermesScenarioAnswerKey:
    root_cause_category: str
    required_keywords: list[str]
    model_response: str
    forbidden_categories: list[str] = field(default_factory=list)
    forbidden_keywords: list[str] = field(default_factory=list)
    required_evidence_sources: list[str] = field(default_factory=list)
    optimal_trajectory: list[str] = field(default_factory=list)
    max_investigation_loops: int = 1


@dataclass(frozen=True)
class HermesScenarioFixture:
    scenario_id: str
    scenario_dir: Path
    alert: dict[str, Any]
    evidence: HermesScenarioEvidence
    metadata: HermesScenarioMetadata
    answer_key: HermesScenarioAnswerKey

    def session_id(self) -> str:
        if self.evidence.hermes_session_log is not None:
            return str(self.evidence.hermes_session_log.get("session_id", ""))
        if self.evidence.hermes_message_history is not None:
            return str(self.evidence.hermes_message_history.get("session_id", ""))
        return ""


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


def _parse_metadata(path: Path) -> HermesScenarioMetadata:
    raw = _read_yaml(path)
    validated: HermesScenarioMetadataSchema = validate_hermes_scenario_metadata(raw)
    return HermesScenarioMetadata(
        schema_version=validated["schema_version"],
        scenario_id=validated["scenario_id"],
        failure_mode=validated["failure_mode"],
        severity=validated["severity"],
        available_evidence=list(validated["available_evidence"]),
        scenario_difficulty=int(validated.get("scenario_difficulty") or 1),
    )


def _parse_answer_key(path: Path) -> HermesScenarioAnswerKey:
    raw = _read_yaml(path)
    validated: HermesScenarioAnswerKeySchema = validate_hermes_answer_key(raw)
    return HermesScenarioAnswerKey(
        root_cause_category=str(validated["root_cause_category"]).strip(),
        required_keywords=[item.strip() for item in validated["required_keywords"]],
        model_response=str(validated["model_response"]).strip(),
        forbidden_categories=list(validated.get("forbidden_categories") or []),
        forbidden_keywords=list(validated.get("forbidden_keywords") or []),
        required_evidence_sources=list(validated.get("required_evidence_sources") or []),
        optimal_trajectory=list(validated.get("optimal_trajectory") or []),
        max_investigation_loops=int(validated.get("max_investigation_loops") or 1),
    )


def _load_evidence(scenario_dir: Path, available_evidence: list[str]) -> HermesScenarioEvidence:
    session_log = None
    provider_traffic = None
    adapter_catalog = None
    hermes_config = None
    runtime_state = None
    message_history = None
    kv_cache_state = None
    cron_state = None
    session_topology = None
    orchestration_state = None
    routing_decisions = None
    memory_state = None
    filesystem_state = None
    audit_trail = None
    approval_events = None
    rbac_state = None
    credential_state = None
    workflow_run = None

    if "hermes_session_log" in available_evidence:
        session_log = validate_hermes_session_log(
            _read_json(scenario_dir / "hermes_session_log.json")
        )

    if "hermes_provider_traffic" in available_evidence:
        provider_traffic = validate_hermes_provider_traffic(
            _read_json(scenario_dir / "hermes_provider_traffic.json")
        )

    if "hermes_adapter_catalog" in available_evidence:
        adapter_catalog = validate_hermes_adapter_catalog(
            _read_json(scenario_dir / "hermes_adapter_catalog.json")
        )

    if "hermes_config" in available_evidence:
        hermes_config = validate_hermes_config(_read_json(scenario_dir / "hermes_config.json"))

    if "hermes_runtime_state" in available_evidence:
        runtime_state = validate_hermes_runtime_state(
            _read_json(scenario_dir / "hermes_runtime_state.json")
        )

    if "hermes_message_history" in available_evidence:
        message_history = validate_hermes_message_history(
            _read_json(scenario_dir / "hermes_message_history.json")
        )

    if "hermes_kv_cache_state" in available_evidence:
        kv_cache_state = validate_hermes_kv_cache_state(
            _read_json(scenario_dir / "hermes_kv_cache_state.json")
        )

    if "hermes_cron_state" in available_evidence:
        cron_state = validate_hermes_cron_state(_read_json(scenario_dir / "hermes_cron_state.json"))

    if "hermes_session_topology" in available_evidence:
        session_topology = validate_hermes_session_topology(
            _read_json(scenario_dir / "hermes_session_topology.json")
        )

    if "hermes_orchestration_state" in available_evidence:
        orchestration_state = validate_hermes_orchestration_state(
            _read_json(scenario_dir / "hermes_orchestration_state.json")
        )

    if "hermes_routing_decisions" in available_evidence:
        routing_decisions = validate_hermes_routing_decisions(
            _read_json(scenario_dir / "hermes_routing_decisions.json")
        )

    if "hermes_memory_state" in available_evidence:
        memory_state = validate_hermes_memory_state(
            _read_json(scenario_dir / "hermes_memory_state.json")
        )

    if "hermes_filesystem_state" in available_evidence:
        filesystem_state = validate_hermes_filesystem_state(
            _read_json(scenario_dir / "hermes_filesystem_state.json")
        )

    if "hermes_audit_trail" in available_evidence:
        audit_trail = validate_hermes_audit_trail(
            _read_json(scenario_dir / "hermes_audit_trail.json")
        )

    if "hermes_approval_events" in available_evidence:
        approval_events = validate_hermes_approval_events(
            _read_json(scenario_dir / "hermes_approval_events.json")
        )

    if "hermes_rbac_state" in available_evidence:
        rbac_state = validate_hermes_rbac_state(_read_json(scenario_dir / "hermes_rbac_state.json"))

    if "hermes_credential_state" in available_evidence:
        credential_state = validate_hermes_credential_state(
            _read_json(scenario_dir / "hermes_credential_state.json")
        )

    if "hermes_workflow_run" in available_evidence:
        workflow_run = validate_hermes_workflow_run(
            _read_json(scenario_dir / "hermes_workflow_run.json")
        )

    return HermesScenarioEvidence(
        hermes_session_log=session_log,
        hermes_provider_traffic=provider_traffic,
        hermes_adapter_catalog=adapter_catalog,
        hermes_config=hermes_config,
        hermes_runtime_state=runtime_state,
        hermes_message_history=message_history,
        hermes_kv_cache_state=kv_cache_state,
        hermes_cron_state=cron_state,
        hermes_session_topology=session_topology,
        hermes_orchestration_state=orchestration_state,
        hermes_routing_decisions=routing_decisions,
        hermes_memory_state=memory_state,
        hermes_filesystem_state=filesystem_state,
        hermes_audit_trail=audit_trail,
        hermes_approval_events=approval_events,
        hermes_rbac_state=rbac_state,
        hermes_credential_state=credential_state,
        hermes_workflow_run=workflow_run,
    )


def load_scenario(scenario_dir: Path) -> HermesScenarioFixture:
    metadata = _parse_metadata(scenario_dir / "scenario.yml")
    answer_key = _parse_answer_key(scenario_dir / "answer.yml")

    required_sources = set(answer_key.required_evidence_sources)
    available_sources = set(metadata.available_evidence)
    missing_required_sources = sorted(required_sources - available_sources)
    if missing_required_sources:
        raise ValueError(
            f"{scenario_dir.name}: answer.yml required_evidence_sources not present in "
            f"scenario.yml available_evidence: {missing_required_sources}"
        )

    alert = validate_hermes_alert(_read_json(scenario_dir / "alert.json"))
    evidence = _load_evidence(scenario_dir, metadata.available_evidence)

    return HermesScenarioFixture(
        scenario_id=metadata.scenario_id,
        scenario_dir=scenario_dir,
        alert=alert,
        evidence=evidence,
        metadata=metadata,
        answer_key=answer_key,
    )


def load_all_scenarios(root_dir: Path | None = None) -> list[HermesScenarioFixture]:
    base_dir = root_dir or SUITE_DIR
    scenario_dirs = sorted(
        path for path in base_dir.iterdir() if path.is_dir() and path.name[:3].isdigit()
    )
    return [load_scenario(path) for path in scenario_dirs]
