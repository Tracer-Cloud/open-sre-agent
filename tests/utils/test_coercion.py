"""Unit tests for app/utils/coercion.py."""

from __future__ import annotations

import pytest

from app.utils.coercion import safe_int


# ---------------------------------------------------------------------------
# safe_int — happy-path inputs
# ---------------------------------------------------------------------------


def test_safe_int_integer_input() -> None:
    """Return the integer unchanged when an int is passed in."""
    assert safe_int(42, default=0) == 42


def test_safe_int_negative_integer() -> None:
    """Return a negative integer unchanged."""
    assert safe_int(-7, default=0) == -7


def test_safe_int_zero() -> None:
    """Return zero when zero is passed in."""
    assert safe_int(0, default=99) == 0


def test_safe_int_string_integer() -> None:
    """Coerce a string that represents a valid integer."""
    assert safe_int("123", default=0) == 123


def test_safe_int_string_negative_integer() -> None:
    """Coerce a string that represents a valid negative integer."""
    assert safe_int("-5", default=0) == -5


def test_safe_int_float_truncates() -> None:
    """Truncate a float value toward zero (standard int() behaviour)."""
    assert safe_int(3.9, default=0) == 3


def test_safe_int_float_string() -> None:
    """Return default for a float-formatted string because int() rejects it."""
    assert safe_int("3.9", default=-1) == -1


def test_safe_int_bool_true() -> None:
    """Treat True as 1 (bool is a subclass of int in Python)."""
    assert safe_int(True, default=0) == 1


def test_safe_int_bool_false() -> None:
    """Treat False as 0 (bool is a subclass of int in Python)."""
    assert safe_int(False, default=99) == 0


# ---------------------------------------------------------------------------
# safe_int — None / empty / wrong type inputs
# ---------------------------------------------------------------------------


def test_safe_int_none_returns_default() -> None:
    """Return default when None is passed in."""
    assert safe_int(None, default=7) == 7


def test_safe_int_empty_string_returns_default() -> None:
    """Return default for an empty string."""
    assert safe_int("", default=42) == 42


def test_safe_int_whitespace_string_returns_default() -> None:
    """Return default for a whitespace-only string."""
    assert safe_int("   ", default=5) == 5


def test_safe_int_non_numeric_string_returns_default() -> None:
    """Return default when the string cannot be converted to int."""
    assert safe_int("abc", default=0) == 0


def test_safe_int_list_returns_default() -> None:
    """Return default when a list is passed in."""
    assert safe_int([1, 2, 3], default=-1) == -1


def test_safe_int_dict_returns_default() -> None:
    """Return default when a dict is passed in."""
    assert safe_int({"key": "value"}, default=-1) == -1


def test_safe_int_object_returns_default() -> None:
    """Return default for an arbitrary object with no __int__ method."""

    class _Opaque:
        pass

    assert safe_int(_Opaque(), default=99) == 99


# ---------------------------------------------------------------------------
# safe_int — boundary / default values
# ---------------------------------------------------------------------------


def test_safe_int_default_zero() -> None:
    """Default of 0 is returned correctly on bad input."""
    assert safe_int("bad", default=0) == 0


def test_safe_int_default_negative() -> None:
    """Negative default is returned correctly on bad input."""
    assert safe_int(None, default=-100) == -100


def test_safe_int_large_integer() -> None:
    """Handle arbitrarily large integers (Python int is unbounded)."""
    big = 10**18
    assert safe_int(big, default=0) == big


def test_safe_int_large_integer_string() -> None:
    """Coerce a very large integer represented as a string."""
    big_str = "9" * 30
    assert safe_int(big_str, default=0) == int(big_str)


def test_safe_int_string_with_leading_trailing_spaces() -> None:
    """int() strips surrounding whitespace, so coercion should succeed."""
    assert safe_int("  10  ", default=0) == 10


@pytest.mark.parametrize(
    "value, default, expected",
    [
        (1, 0, 1),
        ("2", 0, 2),
        (None, 3, 3),
        ("", 4, 4),
        ("nope", 5, 5),
        (0, 99, 0),
        (-1, 0, -1),
        (3.7, 0, 3),
    ],
)
def test_safe_int_parametrized(value: object, default: int, expected: int) -> None:
    """Parametrized smoke-test covering common value/default combinations."""
    assert safe_int(value, default=default) == expected
