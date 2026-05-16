#!/usr/bin/env python3
"""Local alert simulation test for the Kubernetes PIPELINE_ERROR scenario.

Runs the bundled Datadog-style fixture through ``run_investigation`` (the same
entry point as ``opensre investigate``), including live Datadog API calls when
credentials are configured.

Alert used:
  [tracer] Pipeline Error in Logs
  PIPELINE_ERROR: Schema validation failed: Missing fields ['customer_id'] in record 0

Usage (from project root):
    make simulate-k8s-alert
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="Requires ANTHROPIC_API_KEY - run manually",
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "datadog_pipeline_error_alert.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def test_kubernetes_local_alert_simulation() -> None:
    """Run a pipeline-error alert locally and verify the report.

    Asserts:
      - root_cause is non-empty and references the missing field
      - slack_message is non-empty and contains a Root Cause section
    """
    from app.pipeline.runners import run_investigation

    fixture = _load_fixture()
    alert = fixture["alert"]

    state = run_investigation(alert)

    root_cause = state.get("root_cause", "") or ""
    slack_message = state.get("slack_message", "") or ""

    print("\n" + "=" * 70)
    print("SIMULATION REPORT OUTPUT")
    print("=" * 70)
    print(slack_message)
    print("=" * 70)
    print(f"\nroot_cause: {root_cause}")

    assert root_cause, "root_cause must be non-empty"
    assert "customer_id" in root_cause.lower(), (
        f"root_cause should reference 'customer_id', got: {root_cause}"
    )

    assert slack_message, "slack_message must be non-empty"
    assert "Root Cause" in slack_message, (
        f"slack_message must contain a Root Cause section.\nGot:\n{slack_message}"
    )
