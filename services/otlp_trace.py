"""Shared parsing for OTLP/JSON trace payloads.

Used by any client that fetches a single trace in OpenTelemetry JSON form
(``{"batches": [{"resource": ..., "scopeSpans": [{"spans": [...]}]}]}``):
the standalone Tempo client and the Grafana Cloud Tempo mixin both consume it.
"""

from __future__ import annotations

from typing import Any


def extract_span_attributes(span: dict[str, Any]) -> dict[str, Any]:
    """Flatten an OTLP attribute list into a plain key -> value mapping.

    Handles the common OTLP/JSON value kinds (string, int, bool, double).
    Attributes without a key or with an unsupported value kind are skipped.
    """
    attributes: dict[str, Any] = {}

    for attr in span.get("attributes", []):
        key = attr.get("key", "")
        if not key:
            continue
        value = attr.get("value", {})

        if "stringValue" in value:
            attributes[key] = value["stringValue"]
        elif "intValue" in value:
            attributes[key] = value["intValue"]
        elif "boolValue" in value:
            attributes[key] = value["boolValue"]
        elif "doubleValue" in value:
            attributes[key] = value["doubleValue"]

    return attributes


def _duration_ms(start_unix_nano: Any, end_unix_nano: Any) -> float:
    """Span duration in milliseconds from OTLP nanosecond timestamps."""
    try:
        start = int(start_unix_nano)
        end = int(end_unix_nano)
    except (TypeError, ValueError):
        return 0.0
    if end <= start:
        return 0.0
    return round((end - start) / 1_000_000, 4)


def parse_otlp_trace(trace_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse an OTLP/JSON trace into a flat list of span dicts.

    The ``service_name`` is lifted from each batch's resource attributes so
    callers can correlate spans to services without a nested lookup.
    """
    spans: list[dict[str, Any]] = []

    for batch in trace_data.get("batches", []):
        if not isinstance(batch, dict):
            continue
        resource_attributes = extract_span_attributes(batch.get("resource", {}))
        service_name = str(resource_attributes.get("service.name", ""))

        for scope in batch.get("scopeSpans", []):
            if not isinstance(scope, dict):
                continue
            for span in scope.get("spans", []):
                if not isinstance(span, dict):
                    continue
                status = span.get("status") or {}
                spans.append(
                    {
                        "name": span.get("name", "unknown"),
                        "span_id": span.get("spanId", ""),
                        "parent_span_id": span.get("parentSpanId", ""),
                        "trace_id": span.get("traceId", ""),
                        "kind": span.get("kind", ""),
                        "service_name": service_name,
                        "duration_ms": _duration_ms(
                            span.get("startTimeUnixNano"),
                            span.get("endTimeUnixNano"),
                        ),
                        "status_code": status.get("code", ""),
                        "status_message": status.get("message", ""),
                        "attributes": extract_span_attributes(span),
                    }
                )

    return spans
