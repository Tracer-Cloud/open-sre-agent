"""Direct unit tests for the memory_mode annotation on ScenarioScore.

Covers the dataclass field default, the annotate_with_memory_mode helper, and
that asdict serialises the field for the --json output path. Pairs with the
axis-memory scaffold landed for issue #1234.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

pytestmark = pytest.mark.synthetic

from tests.synthetic.rds_postgres.run_suite import (
    ScenarioScore,
    annotate_with_memory_mode,
)


def _make_score(memory_mode: str | None = None) -> ScenarioScore:
    return ScenarioScore(
        scenario_id="002-connection-exhaustion",
        passed=True,
        root_cause_present=True,
        expected_category="resource_exhaustion",
        actual_category="resource_exhaustion",
        missing_keywords=[],
        matched_keywords=["connection", "max_connections"],
        root_cause="connection exhaustion",
        memory_mode=memory_mode,
    )


def test_memory_mode_defaults_to_none() -> None:
    """An unannotated ScenarioScore has memory_mode=None."""
    score = _make_score()
    assert score.memory_mode is None


def test_memory_mode_can_be_constructed_directly() -> None:
    """memory_mode can be set at construction time as a string."""
    score = _make_score(memory_mode="memory_neutral")
    assert score.memory_mode == "memory_neutral"


def test_annotate_with_memory_mode_returns_new_instance() -> None:
    """The helper returns a new ScenarioScore (frozen dataclass)."""
    original = _make_score()
    annotated = annotate_with_memory_mode(original, "memory_hurt")
    assert annotated.memory_mode == "memory_hurt"
    # Original is unchanged because the dataclass is frozen.
    assert original.memory_mode is None
    # All other fields are preserved.
    assert annotated.scenario_id == original.scenario_id
    assert annotated.passed == original.passed
    assert annotated.matched_keywords == original.matched_keywords


@pytest.mark.parametrize(
    "memory_mode",
    [
        "memory_helped",
        "memory_hurt",
        "memory_neutral",
        "pre_existing",
        "not_run",
    ],
)
def test_annotate_accepts_every_documented_mode(memory_mode: str) -> None:
    """Every memory mode documented in the dataclass docstring round-trips."""
    score = annotate_with_memory_mode(_make_score(), memory_mode)
    assert score.memory_mode == memory_mode


def test_asdict_serialises_memory_mode() -> None:
    """asdict (used by the --json output path) emits memory_mode."""
    score = _make_score(memory_mode="memory_neutral")
    payload = asdict(score)
    assert payload["memory_mode"] == "memory_neutral"


def test_asdict_serialises_none_memory_mode() -> None:
    """The default None case is also emitted, not skipped."""
    payload = asdict(_make_score())
    assert "memory_mode" in payload
    assert payload["memory_mode"] is None


@pytest.mark.parametrize(
    "bad_mode",
    ["", "neutral", "memory_HELPED", "MEMORY_NEUTRAL", "garbage", "helped"],
)
def test_annotate_rejects_unknown_mode(bad_mode: str) -> None:
    """An unknown memory_mode string raises ValueError rather than silently storing."""
    with pytest.raises(ValueError, match="unknown memory_mode"):
        annotate_with_memory_mode(_make_score(), bad_mode)  # type: ignore[arg-type]
