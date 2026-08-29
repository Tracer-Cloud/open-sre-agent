from __future__ import annotations

CORE_JVM_RUNTIME_METRICS = (
    "jvm_gc_count_delta",
    "jvm_gc_time_delta",
    "jvm_gc_fgc_count",
    "jvm_gc_fgc_time",
    "jvm_mem_heap_usage",
    "jvm_mem_pools_g1_old_gen_usage",
    "jvm_thread_count",
    "jvm_thread_deadlock_count",
)

RUNTIME_PROBE_CASE_TYPES = {
    "cpu",
    "hsf",
    "jvm",
    "自定义监控",
}

RUNTIME_PROBE_MARKERS = (
    "cpu",
    "fullgc",
    "gc",
    "hsf",
    "oom",
    "pod",
    "threadpool",
    "timeout",
    "container",
    "耗时",
    "失败数",
    "成功率",
    "线程池",
)


def should_probe_runtime_metrics(case_type: str, signal_text: str) -> bool:
    """Return whether JVM/runtime metrics should be sampled for a case."""

    normalized_case_type = case_type.strip().lower()
    if normalized_case_type in RUNTIME_PROBE_CASE_TYPES:
        return True
    lower = signal_text.lower()
    return any(marker in lower for marker in RUNTIME_PROBE_MARKERS)


def runtime_metric_names(case_type: str, signal_text: str) -> tuple[str, ...]:
    """Return current sf JVM metric names for runtime RCA evidence."""

    if not should_probe_runtime_metrics(case_type, signal_text):
        return ()
    return CORE_JVM_RUNTIME_METRICS
