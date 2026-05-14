from __future__ import annotations

from app.integrations.catalog import classify_integrations
from app.nodes.plan_actions.detect_sources import detect_sources


def test_classify_servicenow_record() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "sn-1",
                "service": "servicenow",
                "status": "active",
                "credentials": {
                    "instance_url": "https://dev12345.service-now.com",
                    "api_token": "token",
                },
            }
        ]
    )

    assert resolved["servicenow"]["instance_url"] == "https://dev12345.service-now.com"
    assert resolved["servicenow"]["api_token"] == "token"
    assert resolved["servicenow"]["integration_id"] == "sn-1"


def test_detect_sources_servicenow_from_annotations() -> None:
    sources = detect_sources(
        {
            "annotations": {
                "servicenow_incident_number": "INC0010001",
                "servicenow_change_query": "business_service=checkout",
            }
        },
        {},
        resolved_integrations={
            "servicenow": {
                "instance_url": "https://dev12345.service-now.com",
                "username": "admin",
                "password": "secret",
                "api_token": "",
                "integration_id": "sn-1",
            }
        },
    )

    assert sources["servicenow"]["incident_id"] == "INC0010001"
    assert sources["servicenow"]["change_query"] == "business_service=checkout"
    assert sources["servicenow"]["connection_verified"] is True


def test_detect_sources_servicenow_extracts_sys_id_from_url() -> None:
    sources = detect_sources(
        {
            "annotations": {
                "servicenow_incident_url": (
                    "https://dev12345.service-now.com/nav_to.do?uri=incident.do?sys_id=abc123"
                )
            }
        },
        {},
        resolved_integrations={
            "servicenow": {
                "instance_url": "https://dev12345.service-now.com",
                "api_token": "token",
            }
        },
    )

    assert sources["servicenow"]["incident_id"] == "abc123"
