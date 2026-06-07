"""Tests for the false-healthy investigation guard (Path B)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.agent.investigation import ConnectedInvestigationAgent
from tests.benchmarks.cloudopsbench.bench_agent import (
    BaselineLLMAloneAgent,
    BenchInvestigationAgent,
)
from tests.benchmarks.cloudopsbench.case_loader import BENCHMARK_DIR
from tests.benchmarks.cloudopsbench.false_healthy_guard import (
    apply_false_healthy_downgrade,
    evidence_shows_unhealthy_workloads,
    investigation_declares_healthy,
    should_downgrade_false_healthy,
)

_BENCH_DIR = BENCHMARK_DIR

# Three tests below read tool_cache.json files from the cloudopsbench corpus,
# which CI runners don't have downloaded by default (matches the gating used
# in test_suite.py / test_performance_alert_localization.py).
_REQUIRES_CORPUS = pytest.mark.skipif(
    not BENCHMARK_DIR.is_dir(),
    reason="CloudOpsBench benchmark data is not downloaded; run "
    "`make download-cloudopsbench-hf` first.",
)


def _pods_entry(output: str) -> dict:
    return {
        "tool_name": "GetResources",
        "tool_args": {"resource_type": "pods", "namespace": "boutique"},
        "data": {"output": output},
    }


@_REQUIRES_CORPUS
def test_evidence_detects_err_image_pull_from_startup_58_corpus() -> None:
    cache = json.loads((_BENCH_DIR / "boutique/startup/58/tool_cache.json").read_text())
    key = 'GetResources:{"resource_type":"pods","name":"","namespace":"boutique"}'
    output = cache[key]
    assert evidence_shows_unhealthy_workloads([_pods_entry(output)])


@_REQUIRES_CORPUS
def test_evidence_ignores_all_running_runtime_3_corpus() -> None:
    """runtime/3 false-healthy is NOT caught by pod-status alone — all Running."""
    cache = json.loads((_BENCH_DIR / "boutique/runtime/3/tool_cache.json").read_text())
    key = 'GetResources:{"resource_type":"pods","name":"","namespace":"boutique"}'
    output = cache[key]
    assert not evidence_shows_unhealthy_workloads([_pods_entry(output)])


def test_investigation_declares_healthy_from_category_or_text() -> None:
    assert investigation_declares_healthy({"root_cause_category": "healthy"})
    assert investigation_declares_healthy(
        {"root_cause": "The cluster appears healthy as no active anomalies were detected."}
    )
    assert not investigation_declares_healthy(
        {"root_cause_category": "unknown", "root_cause": "OOM on cartservice"}
    )


@_REQUIRES_CORPUS
def test_should_downgrade_when_healthy_claim_conflicts_with_pods() -> None:
    cache = json.loads((_BENCH_DIR / "boutique/startup/58/tool_cache.json").read_text())
    key = 'GetResources:{"resource_type":"pods","name":"","namespace":"boutique"}'
    updates = {
        "root_cause_category": "healthy",
        "root_cause": "No issues found.",
        "evidence_entries": [_pods_entry(cache[key])],
    }
    assert should_downgrade_false_healthy(updates)


def test_apply_downgrade_sets_unknown_category_and_unresolved_text() -> None:
    downgraded = apply_false_healthy_downgrade(
        {
            "root_cause_category": "healthy",
            "root_cause": "All clear.",
            "report": "Pods look fine.",
        }
    )
    assert downgraded["root_cause_category"] == "unknown"
    assert "unresolved" in downgraded["root_cause"].lower()
    assert "rejected" in downgraded["report"].lower()


def test_bench_agent_downgrades_after_super_run() -> None:
    healthy_updates = {
        "root_cause_category": "healthy",
        "root_cause": "cluster appears healthy",
        "report": "ok",
        "evidence_entries": [
            _pods_entry("frontend-abc  0/1  ErrImagePull  0  10s"),
        ],
    }
    agent = BenchInvestigationAgent()
    with patch.object(
        ConnectedInvestigationAgent,
        "run",
        return_value=healthy_updates,
    ):
        result = agent.run({})
    assert result["root_cause_category"] == "unknown"
    assert "unresolved" in result["root_cause"].lower()


def test_baseline_agent_does_not_downgrade() -> None:
    """Control arm must not inherit the false-healthy guard."""
    healthy_updates = {
        "root_cause_category": "healthy",
        "root_cause": "cluster appears healthy",
        "evidence_entries": [
            _pods_entry("frontend-abc  0/1  ErrImagePull  0  10s"),
        ],
    }
    agent = BaselineLLMAloneAgent()
    with patch.object(
        ConnectedInvestigationAgent,
        "run",
        return_value=healthy_updates,
    ):
        result = agent.run({})
    assert result["root_cause_category"] == "healthy"
