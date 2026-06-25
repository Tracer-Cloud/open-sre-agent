"""Tests for no-op tracing helpers."""

from __future__ import annotations

from platform.observability.tracing import traceable


def test_traceable_returns_identity_decorator() -> None:
    def traced_function() -> str:
        return "ok"

    # Identity check is intentional: traceable() is a no-op and must
    # return the original function unchanged, not a functools.wraps wrapper
    assert traceable()(traced_function) is traced_function
    assert traceable("investigation-step", extra="x")(traced_function) is traced_function


def test_traceable_preserves_args_kwargs_return_value_and_metadata() -> None:
    @traceable("span-name")
    def traced_function(value: int, *, suffix: str) -> str:
        """Original docstring."""
        return f"{value}{suffix}"

    assert traced_function(7, suffix="ms") == "7ms"
    assert traced_function.__name__ == "traced_function"
    assert traced_function.__doc__ == "Original docstring."
