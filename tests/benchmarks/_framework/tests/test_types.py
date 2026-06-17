"""Regression tests for the shared data contracts in ``_framework/types.py``."""

from __future__ import annotations

from tests.benchmarks._framework.types import CaseFilters


def test_case_filters_seed_round_trips() -> None:
    """Guards the Pydantic silent-drop bug class.

    Pydantic v2 BaseModel defaults to ``extra='ignore'``, so removing the
    ``seed`` field would let ``CaseFilters(seed=42)`` succeed silently and
    crash later at every ``filters.seed`` access in adapter / runner /
    smoke paths. This test fails fast if the field is ever dropped again.
    """
    assert CaseFilters(seed=42).seed == 42


def test_case_filters_seed_defaults_to_none() -> None:
    """Default ``seed=None`` keeps the no-shuffle path working when callers
    omit the kwarg (smoke runs, ad-hoc adapter tests)."""
    assert CaseFilters().seed is None
