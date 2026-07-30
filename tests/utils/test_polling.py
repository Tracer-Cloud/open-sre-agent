"""Unit tests for the wait_until polling helper."""

from __future__ import annotations

import pytest

from tests.utils.polling import wait_until


def test_wait_until_returns_once_predicate_becomes_true() -> None:
    calls = {"count": 0}

    def predicate() -> bool:
        calls["count"] += 1
        return calls["count"] >= 3

    wait_until(predicate, timeout=1, interval=0.01)

    assert calls["count"] == 3


def test_wait_until_raises_timeout_error_when_predicate_never_true() -> None:
    with pytest.raises(TimeoutError):
        wait_until(lambda: False, timeout=0.05, interval=0.01)
