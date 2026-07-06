"""Unit tests for shared alert field helpers."""

from __future__ import annotations

from core.domain.alerts.fields import (
    alert_annotations,
    alert_labels,
    alert_name_value,
    dict_value,
    first_present,
    iter_alert_blocks,
    pipeline_name_value,
    severity_value,
)


def test_dict_value_returns_copy_for_mapping_values() -> None:
    source = {"labels": {"service": "api"}}

    value = dict_value(source, "labels")
    value["service"] = "worker"

    assert source["labels"]["service"] == "api"


def test_first_present_keeps_falsey_non_blank_values() -> None:
    assert first_present(None, "", "  ", 0, "fallback") == 0
    assert first_present(None, "", False, "fallback") is False


def test_iter_alert_blocks_uses_shared_order() -> None:
    raw_alert = {
        "labels": {"source": "labels"},
        "commonAnnotations": {"source": "commonAnnotations"},
        "annotations": {"source": "annotations"},
        "commonLabels": {"source": "commonLabels"},
    }

    assert [block["source"] for block in iter_alert_blocks(raw_alert)] == [
        "commonAnnotations",
        "annotations",
        "commonLabels",
        "labels",
    ]


def test_alert_label_and_annotation_helpers_prefer_common_blocks() -> None:
    raw_alert = {
        "commonLabels": {"service": "api"},
        "labels": {"service": "worker"},
        "commonAnnotations": {"summary": "common"},
        "annotations": {"summary": "fallback"},
    }

    assert alert_labels(raw_alert) == {"service": "api"}
    assert alert_annotations(raw_alert) == {"summary": "common"}


def test_alert_field_precedence_uses_raw_then_canonical_then_blocks() -> None:
    raw_alert = {
        "title": "Raw title",
        "service": "raw-service",
        "canonical_alert": {
            "alert_name": "Canonical name",
            "pipeline_name": "canonical-pipeline",
            "severity": "critical",
        },
        "commonLabels": {
            "alertname": "Label name",
            "service": "label-service",
            "severity": "warning",
        },
        "commonAnnotations": {
            "summary": "Annotation summary",
            "pipeline_name": "annotation-pipeline",
        },
    }
    labels = alert_labels(raw_alert)
    annotations = alert_annotations(raw_alert)
    canonical = dict_value(raw_alert, "canonical_alert")

    assert (
        alert_name_value(
            raw_alert,
            labels=labels,
            annotations=annotations,
            canonical=canonical,
        )
        == "Raw title"
    )
    assert (
        pipeline_name_value(
            raw_alert,
            labels=labels,
            annotations=annotations,
            canonical=canonical,
        )
        == "canonical-pipeline"
    )
    assert severity_value(raw_alert, labels=labels, canonical=canonical) == "critical"
