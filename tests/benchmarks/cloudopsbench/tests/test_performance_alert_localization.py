"""Tests for alert-driven performance localization."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.benchmarks.cloudopsbench.case_loader import BENCHMARK_DIR, case_root
from tests.benchmarks.cloudopsbench.performance_alert_localization import (
    format_metric_alerts,
    infer_performance_localization,
    load_alert_json,
    performance_context_for_case_dir,
)

pytestmark = [
    pytest.mark.cloudopsbench,
    pytest.mark.skipif(
        not BENCHMARK_DIR.is_dir(),
        reason="CloudOpsBench benchmark data is not downloaded; run "
        "`make download-cloudopsbench-hf` first.",
    ),
]

_BENCH = BENCHMARK_DIR


@pytest.mark.parametrize(
    ("case_id", "expected_object", "expected_rc"),
    [
        ("boutique/performance/1", "app/adservice", "pod_cpu_overload"),
        ("boutique/performance/20", "app/recommendationservice", "pod_network_delay"),
        ("trainticket/performance/33", "app/ts-travel-service", "pod_network_delay"),
        ("trainticket/performance/44", "app/ts-station-service", "pod_network_delay"),
        ("trainticket/performance/9", "app/ts-price-service", "pod_network_delay"),
        ("trainticket/performance/23", "app/ts-inside-payment-service", "pod_network_delay"),
    ],
)
def test_infer_performance_localization_matches_ground_truth_cases(
    case_id: str, expected_object: str, expected_rc: str
) -> None:
    system, category, name = case_id.split("/")
    case_dir = case_root(system, category, name, _BENCH)
    alert_data = load_alert_json(case_dir)
    assert alert_data is not None
    hint = infer_performance_localization(alert_data, namespace=system)
    assert hint is not None
    assert hint["fault_object"] == expected_object
    assert hint["root_cause"] == expected_rc


def test_format_metric_alerts_includes_service_lines() -> None:
    case_dir = case_root("trainticket", "performance", "44", _BENCH)
    alert_data = load_alert_json(case_dir)
    text = format_metric_alerts(alert_data)
    assert "ts-station-service" in text
    assert "LATENCY_DEGRADATION" in text or "35691" in text


def test_performance_context_for_case_dir() -> None:
    case_dir = case_root("boutique", "performance", "1", _BENCH)
    alerts, hint = performance_context_for_case_dir(case_dir, namespace="boutique")
    assert "adservice" in alerts
    assert hint is not None
    assert hint["root_cause"] == "pod_cpu_overload"


def test_infer_returns_none_for_missing_alerts(tmp_path: Path) -> None:
    assert infer_performance_localization(None, namespace="boutique") is None
    assert infer_performance_localization({"alerts": []}, namespace="boutique") is None
