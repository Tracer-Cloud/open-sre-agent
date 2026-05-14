#!/usr/bin/env python3
"""Accuracy fixture test for the Kubernetes etl-transform job failure.

Asserts that the fixture still encodes the configuration-error signal the
current agent pipeline should recover from during live RCA.

Usage (from project root):
    python -m pytest tests/e2e/kubernetes/accurate_rca_test.py -s
"""

from __future__ import annotations

import json
from pathlib import Path

from app.state import make_initial_state

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "datadog_k8s_alert.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def test_accurate_kubernetes_rca_fixture_contains_configuration_error_signal():
    """Assert the fixture contains the expected configuration-error evidence."""
    fixture = _load_fixture()

    state = make_initial_state(raw_alert=fixture["alert"])
    evidence = fixture["evidence"]
    error_messages = [entry["message"] for entry in evidence["datadog_error_logs"]]

    assert state["raw_alert"]["alert_id"] == fixture["alert"]["alert_id"]
    assert any("Schema validation failed" in message for message in error_messages)
    assert any("payment_method" in message for message in error_messages)
    assert "REQUIRED_FIELDS" in fixture["alert"]["message"]
