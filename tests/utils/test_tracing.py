"""Tests for the no-op traceable decorator."""

from __future__ import annotations

from app.utils.tracing import traceable


def test_traceable_returns_identity_decorator() -> None:
    def sample() -> str:
        return "ok"

    # Object identity is intentional: traceable is a no-op, not a wrapper.
    assert traceable()(sample) is sample
    assert traceable("span-name", extra="metadata")(sample) is sample


def test_traceable_preserves_callable_metadata_and_behavior() -> None:
    def documented(a: int, b: int, *, flag: bool = False) -> int:
        """Add two integers."""
        return a + b if flag else a - b

    traced = traceable("documented-span")(documented)

    assert traced.__name__ == "documented"
    assert traced.__doc__ == "Add two integers."
    assert traced(5, 2) == 3
    assert traced(5, 2, flag=True) == 7
