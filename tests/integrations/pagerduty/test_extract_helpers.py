"""Unit tests for PagerDuty response extraction helpers."""

import pytest

from integrations.pagerduty.client import _extract_priority, _extract_ref


@pytest.mark.parametrize("value", [None, {}])
def test_extract_ref_returns_empty_dict_when_missing(value: dict[str, str] | None) -> None:
    assert _extract_ref(value) == {}


def test_extract_ref_returns_compact_reference() -> None:
    ref = {"id": "P123", "summary": "Primary", "type": "service", "extra": "ignored"}

    assert _extract_ref(ref) == {"id": "P123", "summary": "Primary", "type": "service"}


def test_extract_ref_fills_missing_fields() -> None:
    assert _extract_ref({"id": "P123"}) == {"id": "P123", "summary": "", "type": ""}


@pytest.mark.parametrize("priority", [None, {}])
def test_extract_priority_returns_empty_dict_when_missing(
    priority: dict[str, str] | None,
) -> None:
    assert _extract_priority({"priority": priority}) == {}


def test_extract_priority_returns_compact_priority() -> None:
    priority = {"id": "P1", "name": "Critical", "summary": "Immediate", "extra": "ignored"}

    assert _extract_priority({"priority": priority}) == {
        "id": "P1",
        "name": "Critical",
        "summary": "Immediate",
    }


def test_extract_priority_fills_missing_fields() -> None:
    assert _extract_priority({"priority": {"name": "Critical"}}) == {
        "id": "",
        "name": "Critical",
        "summary": "",
    }
