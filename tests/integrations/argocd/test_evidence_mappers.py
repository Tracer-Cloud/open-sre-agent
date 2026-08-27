from typing import Any

import pytest

from core.tool_framework.utils import tool_unavailable
from integrations.argocd.tools import (
    _map_argocd_application_diff,
    _map_argocd_application_status,
)


def test_diff_mapper_records_an_entry_when_diffs_present() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "argocd",
        "available": True,
        "success": True,
        "application_name": "payment-service",
        "drift_detected": True,
        "diffs": [
            {"kind": "Deployment", "name": "payment-svc"},
            {"kind": "ConfigMap", "name": "payment-config"},
        ],
        "diff_count": 2,
    }

    _map_argocd_application_diff(evidence, output, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "argocd_application_diff",
            "label": "Argo CD Application Diff",
            "summary": "payment-service: 2 drifted resources",
            "url": None,
            "snippet": None,
        }
    ]


def test_diff_mapper_records_drift_detected_when_diffs_list_is_empty() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "argocd",
        "available": True,
        "success": True,
        "application_name": "frontend",
        "drift_detected": True,
        "diffs": [],
        "diff_count": 0,
    }

    _map_argocd_application_diff(evidence, output, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "argocd_application_diff",
            "label": "Argo CD Application Diff",
            "summary": "frontend: drift detected (no resource-level diffs)",
            "url": None,
            "snippet": None,
        }
    ]


@pytest.mark.parametrize(
    "output",
    [
        pytest.param({}, id="empty-payload"),
        pytest.param(
            tool_unavailable(
                "argocd",
                "connection refused",
                diffs=[],
                diff_count=0,
                drift_detected=False,
            ),
            id="unavailable-envelope",
        ),
        pytest.param(
            {"source": "argocd", "available": True, "diffs": [], "drift_detected": False},
            id="no-drift",
        ),
    ],
)
def test_diff_mapper_records_nothing_when_no_drift(output: dict[str, Any]) -> None:
    evidence: dict[str, Any] = {}
    _map_argocd_application_diff(evidence, output, {})
    assert "catalog_entries" not in evidence


def test_status_mapper_records_single_app_sync_and_health() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "argocd",
        "available": True,
        "success": True,
        "application": {
            "name": "payment-service",
            "sync_status": "OutOfSync",
            "health_status": "Degraded",
        },
    }

    _map_argocd_application_status(evidence, output, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "argocd_application_status",
            "label": "Argo CD Application Status",
            "summary": "payment-service: sync=OutOfSync, health=Degraded",
            "url": None,
            "snippet": None,
        }
    ]


def test_status_mapper_records_application_list() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "argocd",
        "available": True,
        "success": True,
        "applications": [
            {"name": "payment-service"},
            {"name": "auth-service"},
            {"name": "order-service"},
        ],
        "total": 3,
    }

    _map_argocd_application_status(evidence, output, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "argocd_application_status",
            "label": "Argo CD Application Status",
            "summary": "3 applications",
            "url": None,
            "snippet": None,
        }
    ]


@pytest.mark.parametrize(
    "output",
    [
        pytest.param({}, id="empty-payload"),
        pytest.param(
            tool_unavailable("argocd", "connection refused", application={}, applications=[]),
            id="unavailable-envelope",
        ),
        pytest.param(
            {"source": "argocd", "available": True, "application": {}},
            id="empty-application",
        ),
        pytest.param(
            {"source": "argocd", "available": True, "applications": []},
            id="empty-applications-list",
        ),
    ],
)
def test_status_mapper_records_nothing_when_empty_or_unavailable(output: dict[str, Any]) -> None:
    evidence: dict[str, Any] = {}
    _map_argocd_application_status(evidence, output, {})
    assert "catalog_entries" not in evidence
