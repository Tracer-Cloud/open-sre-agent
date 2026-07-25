"""Tests for Grafana alert-derived Loki query scope."""

from __future__ import annotations

from config.constants import INVESTIGATION_CONTEXT_SOURCE_KEY
from integrations.grafana.alert_context import resolve_grafana_loki_alert_context


def test_resolves_common_grafana_webhook_fields() -> None:
    context = resolve_grafana_loki_alert_context(
        {
            INVESTIGATION_CONTEXT_SOURCE_KEY: {
                "raw_alert": {
                    "commonLabels": {
                        "service_name": "checkout",
                        "pipeline_name": "orders",
                        "datasource_uid": "loki-prod",
                    },
                    "commonAnnotations": {
                        "log_query": '{service_name="checkout"} |= "failed"',
                    },
                }
            }
        }
    )

    assert context.service_name == "checkout"
    assert context.pipeline_name == "orders"
    assert context.log_query == '{service_name="checkout"} |= "failed"'
    assert context.datasource_uid == "loki-prod"


def test_raw_alert_precedes_normalized_alert_json() -> None:
    context = resolve_grafana_loki_alert_context(
        {
            INVESTIGATION_CONTEXT_SOURCE_KEY: {
                "raw_alert": {"labels": {"service_name": "source-service"}},
                "alert_json": {
                    "service_name": "model-service",
                    "log_query": '{service_name="model-service"}',
                },
            }
        }
    )

    assert context.service_name == "source-service"
    assert context.log_query == '{service_name="model-service"}'


def test_resolves_fields_from_individual_alert_when_common_fields_are_absent() -> None:
    context = resolve_grafana_loki_alert_context(
        {
            INVESTIGATION_CONTEXT_SOURCE_KEY: {
                "raw_alert": {
                    "alerts": [
                        {
                            "labels": {
                                "service": "payments",
                                "pipeline": "settlement",
                                "loki_datasource_uid": "loki-eu",
                            },
                            "annotations": {
                                "logql": '{service="payments"} | json',
                            },
                        }
                    ]
                }
            }
        }
    )

    assert context.service_name == "payments"
    assert context.pipeline_name == "settlement"
    assert context.log_query == '{service="payments"} | json'
    assert context.datasource_uid == "loki-eu"


def test_does_not_treat_metric_datasource_uid_as_loki() -> None:
    context = resolve_grafana_loki_alert_context(
        {
            INVESTIGATION_CONTEXT_SOURCE_KEY: {
                "raw_alert": {
                    "commonLabels": {
                        "service_name": "checkout",
                        "datasource_uid": "grafanacloud-prom",
                    }
                }
            }
        }
    )

    assert context.service_name == "checkout"
    assert context.datasource_uid == ""
