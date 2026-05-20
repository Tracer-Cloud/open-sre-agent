"""Deterministic router contracts for slash and bare-alias command input."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import pytest
import yaml

from app.cli.interactive_shell.routing.router import classify_input, route_input
from app.cli.interactive_shell.runtime.session import ReplSession

TESTS_DIR = Path(__file__).resolve().parent


class RouterContractCase(TypedDict):
    id: str
    input: str
    expected_kind: str
    expected_signals: list[str]
    expected_command_text: str | None


def _load_contract_cases(filename: str) -> list[RouterContractCase]:
    payload = yaml.safe_load((TESTS_DIR / filename).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        msg = f"Fixture {filename} must contain a top-level YAML list"
        raise ValueError(msg)
    validated: list[RouterContractCase] = []
    for idx, row in enumerate(payload):
        if not isinstance(row, dict):
            msg = f"Fixture {filename} row {idx} must be a mapping"
            raise ValueError(msg)
        validated.append(
            RouterContractCase(
                id=str(row["id"]),
                input=str(row["input"]),
                expected_kind=str(row["expected_kind"]),
                expected_signals=[str(signal) for signal in list(row["expected_signals"])],
                expected_command_text=(
                    str(row["expected_command_text"])
                    if row["expected_command_text"] is not None
                    else None
                ),
            )
        )
    return validated


@pytest.mark.parametrize(
    "case",
    _load_contract_cases("router_contracts.yml"),
    ids=lambda case: case["id"],
)
def test_router_contract_cases(case: RouterContractCase) -> None:
    session = ReplSession()

    decision = route_input(case["input"], session)
    assert classify_input(case["input"], session) == case["expected_kind"]

    assert decision.route_kind.value == case["expected_kind"]
    assert decision.matched_signals == tuple(case["expected_signals"])
    assert decision.command_text == case["expected_command_text"]


def test_help_route_decision_has_structured_shape() -> None:
    session = ReplSession()
    decision = route_input("/help", session)

    assert decision.to_event_payload() == {
        "route_kind": "slash",
        "confidence": 1.0,
        "matched_signals": "slash_prefix",
        "fallback_reason": "",
    }
    assert decision.command_text == "/help"
