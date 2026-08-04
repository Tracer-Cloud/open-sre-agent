"""Unit tests for the PagerDuty payload extract helpers (C-44).

`_extract_ref` and `_extract_priority` normalise raw PagerDuty API objects into
compact dicts used across the incident/service/on-call parsers. They must be
null-safe and default missing fields to empty strings — these tests pin that
contract for present, missing, and edge-case inputs (no live API calls).
"""

from __future__ import annotations

from integrations.pagerduty.client import _extract_priority, _extract_ref

# --- _extract_ref ---


def test_extract_ref_full_object() -> None:
    ref = {"id": "PABC123", "summary": "Payments API", "type": "service_reference"}
    assert _extract_ref(ref) == {
        "id": "PABC123",
        "summary": "Payments API",
        "type": "service_reference",
    }


def test_extract_ref_none_returns_empty() -> None:
    assert _extract_ref(None) == {}


def test_extract_ref_empty_dict_returns_empty() -> None:
    # Falsy dict short-circuits before field extraction.
    assert _extract_ref({}) == {}


def test_extract_ref_missing_fields_default_to_empty_string() -> None:
    # A truthy-but-partial ref keeps every key, defaulting absent ones to "".
    assert _extract_ref({"id": "P1"}) == {"id": "P1", "summary": "", "type": ""}


def test_extract_ref_ignores_unknown_keys() -> None:
    ref = {"id": "P1", "summary": "s", "type": "t", "html_url": "http://x", "self": "y"}
    assert _extract_ref(ref) == {"id": "P1", "summary": "s", "type": "t"}


# --- _extract_priority ---


def test_extract_priority_present() -> None:
    incident = {"priority": {"id": "PRI1", "name": "P1", "summary": "Critical"}}
    assert _extract_priority(incident) == {
        "id": "PRI1",
        "name": "P1",
        "summary": "Critical",
    }


def test_extract_priority_null_priority_returns_empty() -> None:
    # PagerDuty sends priority: null for unprioritised incidents.
    assert _extract_priority({"priority": None}) == {}


def test_extract_priority_absent_key_returns_empty() -> None:
    assert _extract_priority({}) == {}


def test_extract_priority_empty_dict_returns_empty() -> None:
    assert _extract_priority({"priority": {}}) == {}


def test_extract_priority_missing_fields_default_to_empty_string() -> None:
    assert _extract_priority({"priority": {"id": "PRI1"}}) == {
        "id": "PRI1",
        "name": "",
        "summary": "",
    }
