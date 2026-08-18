from __future__ import annotations

from integrations.pagerduty.client import _extract_priority, _extract_ref


def test_extract_ref_prefers_id_summary_type() -> None:
    obj = {
        "id": "PXXXXX",
        "summary": "Service A",
        "type": "service_reference",
        "self": "https://api.pagerduty.com/services/PXXXXX",
    }
    assert _extract_ref(obj) == {
        "id": "PXXXXX",
        "summary": "Service A",
        "type": "service_reference",
    }


def test_extract_ref_handles_missing_fields_with_empty_strings() -> None:
    obj = {"id": "PYYYYY"}
    assert _extract_ref(obj) == {
        "id": "PYYYYY",
        "summary": "",
        "type": "",
    }


def test_extract_ref_returns_empty_dict_for_missing_or_empty_object() -> None:
    assert _extract_ref(None) == {}
    assert _extract_ref({}) == {}


def test_extract_priority_prefers_id_name_summary() -> None:
    incident = {
        "priority": {
            "id": "P123",
            "name": "P1",
            "summary": "P1",
            "type": "priority",
        }
    }
    assert _extract_priority(incident) == {
        "id": "P123",
        "name": "P1",
        "summary": "P1",
    }


def test_extract_priority_handles_missing_fields_with_empty_strings() -> None:
    incident = {
        "priority": {
            "id": "P123",
        }
    }
    assert _extract_priority(incident) == {
        "id": "P123",
        "name": "",
        "summary": "",
    }


def test_extract_priority_returns_empty_dict_for_missing_or_empty_priority() -> None:
    assert _extract_priority({"priority": None}) == {}
    assert _extract_priority({}) == {}
