from __future__ import annotations

from tests.benchmarks.realrca_graph.runtime_metrics import (
    CORE_JVM_RUNTIME_METRICS,
    runtime_metric_names,
    should_probe_runtime_metrics,
)


def test_runtime_metrics_probe_custom_monitor_business_failure() -> None:
    text = "success rate dropped with HSF timeout and failure count"

    assert should_probe_runtime_metrics("自定义监控", text) is True
    assert "jvm_gc_time_delta" in runtime_metric_names("自定义监控", text)


def test_runtime_metrics_skip_unrelated_case_without_runtime_markers() -> None:
    assert should_probe_runtime_metrics("TDDL", "slow sql table write rt") is False
    assert runtime_metric_names("TDDL", "slow sql table write rt") == ()
    assert should_probe_runtime_metrics("OTHER", "ordinary business event") is False


def test_runtime_metric_catalog_uses_current_sf_jvm_names() -> None:
    assert "jvm_gc_count_delta" in CORE_JVM_RUNTIME_METRICS
    assert "jvm_mem_heap_usage" in CORE_JVM_RUNTIME_METRICS
    assert "jvm_memory_pool_used" not in CORE_JVM_RUNTIME_METRICS
