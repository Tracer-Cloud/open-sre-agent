"""Unit tests for K8s trigger alert Datadog verification helpers."""

from __future__ import annotations

from tests.e2e.kubernetes.trigger_alert import (
    DD_SINCE_EPOCH_BUFFER_SECONDS,
    _build_datadog_search_payload,
    datadog_log_search_window,
)


def test_datadog_log_search_window_anchors_on_since_epoch() -> None:
    since_epoch = 1_700_000_000.0
    from_value, to_value = datadog_log_search_window(since_epoch)
    expected_from = int((since_epoch - DD_SINCE_EPOCH_BUFFER_SECONDS) * 1000)
    assert from_value == str(expected_from)
    assert to_value == "now"


def test_datadog_log_search_window_without_since_epoch_uses_broader_relative_window() -> None:
    from_value, to_value = datadog_log_search_window(None)
    assert from_value == "now-15m"
    assert to_value == "now"


def test_datadog_log_search_window_without_since_epoch_honors_now_epoch() -> None:
    now_epoch = 1_700_000_000.0
    from_value, to_value = datadog_log_search_window(None, now_epoch=now_epoch)
    expected_from = int((now_epoch - 15 * 60) * 1000)
    assert from_value == str(expected_from)
    assert to_value == "now"


def test_build_datadog_search_payload_includes_pipeline_error_query() -> None:
    payload = _build_datadog_search_payload(1_700_000_000.0)
    assert payload["filter"]["query"] == "kube_namespace:tracer-test PIPELINE_ERROR"  # type: ignore[index]
