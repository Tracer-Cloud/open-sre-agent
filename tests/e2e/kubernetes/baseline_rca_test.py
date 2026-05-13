#!/usr/bin/env python3
"""Baseline fixture test for the Kubernetes etl-transform job failure.

The old node-level RCA entry points were removed with the graph runtime. This
test now keeps the fixture contract alive for the current agent pipeline.

Usage (from project root):
    python -m pytest tests/e2e/kubernetes/baseline_rca_test.py -s
"""

from __future__ import annotations

import json
from pathlib import Path

from app.state import make_initial_state

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "datadog_k8s_alert.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def test_baseline_kubernetes_rca_fixture_shape():
    """The fixture carries enough structured evidence for the agent pipeline."""
    fixture = _load_fixture()

    state = make_initial_state(raw_alert=fixture["alert"])
    evidence = fixture["evidence"]

    has_k8s_tags = any(
        any(t.startswith("kube_") for t in log.get("tags", []) if isinstance(t, str))
        for log in evidence.get("datadog_logs", [])
    )

    assert state["raw_alert"]["alert_id"] == fixture["alert"]["alert_id"]
    assert evidence["datadog_error_logs"]
    assert "Missing fields ['payment_method']" in evidence["datadog_error_logs"][0]["message"]
    assert has_k8s_tags is True
