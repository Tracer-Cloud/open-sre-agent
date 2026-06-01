from __future__ import annotations

import json
from pathlib import Path

from tests.synthetic.hermes_rca.analog_registry import analog_ids
from tests.synthetic.hermes_rca.surface_scoring import VALID_SURFACE_FAMILIES

TUPLES_PATH = (
    Path(__file__).resolve().parent / "050-surface-sprawl-unknown-adapter" / "adapter_tuples.json"
)


def _load_tuples() -> list[dict[str, str]]:
    payload = json.loads(TUPLES_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def test_surface_adapter_tuples_cover_at_least_20_cases() -> None:
    tuples = _load_tuples()

    assert len(tuples) >= 20


def test_surface_adapter_tuples_have_required_fields() -> None:
    required = {
        "id",
        "messaging_adapter",
        "llm_provider",
        "execution_backend",
        "failing_surface",
        "failing_adapter",
        "expected_family",
        "expected_analog_id",
    }

    for item in _load_tuples():
        assert required <= set(item), item
        assert all(isinstance(item[field], str) and item[field].strip() for field in required)


def test_surface_adapter_tuples_reference_known_families_and_analogs() -> None:
    known_analogs = analog_ids()

    for item in _load_tuples():
        assert item["expected_family"] in VALID_SURFACE_FAMILIES
        assert item["failing_surface"] in VALID_SURFACE_FAMILIES
        assert item["expected_analog_id"] in known_analogs


def test_surface_adapter_tuples_have_unique_ids() -> None:
    ids = [item["id"] for item in _load_tuples()]

    assert len(ids) == len(set(ids))
