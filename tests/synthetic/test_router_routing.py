"""Synthetic real-LLM routing proof for ROUTER_PROMPT.

Required-Proof tests for issue #656. These exercise the live LLM via
``router_node`` against the synthetic alert fixtures and a hand-curated
list of conceptual SRE prompts. Gated by ``@pytest.mark.synthetic`` so
they only run under ``make test-synthetic`` (and require a real
provider API key in the environment).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.nodes import chat as chat_mod

_RDS_ALERTS_DIR = Path(__file__).parent / "rds_postgres"
_EKS_ALERTS_DIR = Path(__file__).parent / "eks"


def _alert_files(suite_dir: Path) -> list[Path]:
    """Return alert.json paths for non-healthy scenarios under *suite_dir*."""
    return sorted(p for p in suite_dir.glob("*/alert.json") if p.parent.name != "000-healthy")


_ALERT_FIXTURES: list[tuple[str, Path]] = [
    *((f"rds:{p.parent.name}", p) for p in _alert_files(_RDS_ALERTS_DIR)),
    *((f"eks:{p.parent.name}", p) for p in _alert_files(_EKS_ALERTS_DIR)),
]


_LLM_ATTEMPTS = 2


def _route_with_retry(state: dict[str, object]) -> str:
    """Invoke router_node up to _LLM_ATTEMPTS times and return the first stable route."""
    last_route = "general"
    for _ in range(_LLM_ATTEMPTS):
        out = chat_mod.router_node(state)  # type: ignore[arg-type]
        last_route = str(out.get("route", "general"))
    return last_route


@pytest.mark.synthetic
@pytest.mark.parametrize(
    ("scenario_id", "alert_path"),
    _ALERT_FIXTURES,
    ids=[fid for fid, _ in _ALERT_FIXTURES],
)
def test_alert_payloads_route_to_tracer_data(scenario_id: str, alert_path: Path) -> None:
    """Every non-healthy synthetic alert.json should route to tracer_data."""
    alert_text = alert_path.read_text()
    state = {"messages": [{"role": "user", "content": alert_text}]}

    route = _route_with_retry(state)

    assert route == "tracer_data", (
        f"{scenario_id}: routed to {route!r} instead of 'tracer_data'\n"
        f"alert title: {json.loads(alert_text).get('title')}"
    )


_GENERAL_PROMPTS = [
    "what is a circuit breaker?",
    "explain the difference between SLI and SLO",
    "what are best practices for incident postmortems?",
    "how should I think about cardinality in metrics?",
    "hi",
]


@pytest.mark.synthetic
@pytest.mark.parametrize("user_message", _GENERAL_PROMPTS)
def test_general_questions_route_to_general(user_message: str) -> None:
    """Conceptual SRE questions and greetings should route to general."""
    state = {"messages": [{"role": "user", "content": user_message}]}

    route = _route_with_retry(state)

    assert route == "general", f"routed to {route!r} instead of 'general' for: {user_message!r}"
