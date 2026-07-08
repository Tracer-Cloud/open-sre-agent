"""Step 3 verification for Vaibhav's datadog_k8s_alert fixture.

Validates the CLI load path, incident window anchoring (including aged alerts),
and the schema-validation RCA signals without requiring a live EKS trigger or
LLM credentials.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from core.domain.alerts.extraction import AlertDetails
from core.domain.types.incident_window import SOURCE_STARTS_AT, resolve_incident_window
from surfaces.cli.investigation.payload import load_file
from tools.investigation.stages.gather_evidence.prompt import format_alert_context
from tools.investigation.stages.intake.node import extract_alert
from tools.investigation.state_factory import make_initial_state
from tools.registry import clear_tool_registry_cache

pytestmark = pytest.mark.e2e

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "datadog_k8s_alert.json"
AGED_NOW = datetime(2026, 2, 19, 4, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_tool_registry() -> Generator[None]:
    clear_tool_registry_cache()
    yield
    clear_tool_registry_cache()


def _load_fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_cli_fixture_path_anchors_incident_window_on_starts_at() -> None:
    payload = load_file(str(FIXTURE_PATH))
    state = make_initial_state(raw_alert=payload)
    window = resolve_incident_window(state["raw_alert"], now=AGED_NOW)

    assert window.source == SOURCE_STARTS_AT
    assert window.confidence == 1.0
    assert window.until == datetime(2026, 2, 19, 0, 10, tzinfo=UTC)


def test_aged_alert_window_covers_fixture_log_timestamps() -> None:
    fixture = _load_fixture()
    window = resolve_incident_window(fixture, now=AGED_NOW)

    for entry in fixture["evidence"]["datadog_logs"]:
        ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
        assert window.since <= ts < window.until


def test_extract_alert_populates_incident_window_from_fixture_envelope() -> None:
    payload = load_file(str(FIXTURE_PATH))
    state = make_initial_state(raw_alert=payload)

    with patch(
        "tools.investigation.stages.intake.node._extract_alert_details",
    ) as extract_details:
        extract_details.return_value = AlertDetails(
            alert_name="Kubernetes job etl-transform-error failed",
            pipeline_name="kubernetes_etl_pipeline",
            severity="critical",
            alert_source="datadog",
            is_noise=False,
        )
        updates = extract_alert(state)

    assert updates["incident_window"]["source"] == SOURCE_STARTS_AT
    assert updates["incident_window"]["confidence"] == 1.0
    assert updates["incident_window"]["until"] == "2026-02-19T00:10:00Z"


def test_gather_evidence_prompt_includes_incident_window_for_k8s_fixture() -> None:
    payload = load_file(str(FIXTURE_PATH))
    state = make_initial_state(raw_alert=payload)
    state["incident_window"] = resolve_incident_window(state["raw_alert"], now=AGED_NOW).to_dict()

    context = format_alert_context(state)

    assert "Incident window: 2026-02-18T22:10:00Z → 2026-02-19T00:10:00Z" in context


def test_fixture_carries_payment_method_schema_validation_signal() -> None:
    fixture = _load_fixture()
    error_messages = [entry["message"] for entry in fixture["evidence"]["datadog_error_logs"]]

    assert any("Schema validation failed" in message for message in error_messages)
    assert any("payment_method" in message for message in error_messages)
    assert "REQUIRED_FIELDS" in fixture["alert"]["message"]
