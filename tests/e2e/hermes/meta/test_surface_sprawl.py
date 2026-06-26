from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.synthetic.hermes_rca.analog_registry import find_analog_by_id
from tests.synthetic.hermes_rca.surface_scoring import score_surface_response

TUPLES_PATH = (
    Path(__file__).resolve().parents[3]
    / "synthetic"
    / "hermes_rca"
    / "050-surface-sprawl-unknown-adapter"
    / "adapter_tuples.json"
)


def _load_adapter_tuples() -> list[dict[str, str]]:
    payload = json.loads(TUPLES_PATH.read_text(encoding="utf-8"))

    assert isinstance(payload, list)

    return payload


def _synthetic_surface_response(item: dict[str, str]) -> str:
    analog = find_analog_by_id(item["expected_analog_id"])

    assert analog is not None

    family_name = item["expected_family"].replace("_", " ")

    return (
        f"This is a {family_name} surface attribution issue involving "
        f"{item['failing_adapter']}. The closest analog is {analog.scenario_id}. "
        f"{analog.diagnostic_question}"
    )


@pytest.mark.e2e
@pytest.mark.parametrize(
    "item",
    _load_adapter_tuples(),
    ids=lambda item: item["id"],
)
def test_surface_sprawl_tuple_scores_attribution_dimensions(
    item: dict[str, str],
) -> None:
    response = _synthetic_surface_response(item)

    score = score_surface_response(
        output=response,
        expected_family=item["expected_family"],
        expected_analog_id=item["expected_analog_id"],
    )

    assert score.passed, (
        f"{item['id']} failed surface attribution scoring: "
        f"family={score.adapter_family}, "
        f"analog={score.analog_identification}, "
        f"diagnostic={score.diagnostic_question}"
    )
