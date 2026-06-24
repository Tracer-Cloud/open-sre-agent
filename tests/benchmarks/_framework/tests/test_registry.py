"""Adapter registry tests: idempotent registration, bootstrap-once
semantics, helpful unknown-name errors.

The bootstrap-once test guards a specific regression: a previous version
checked ``if known_adapters(): return`` as the "already bootstrapped"
sentinel, which silently skipped the canonical adapter imports any time
a test or other code path had pre-registered something. The current
``_REGISTRY_STATE["bootstrapped"]`` sentinel decouples those two states.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import patch

import pytest

from tests.benchmarks._framework import registry
from tests.benchmarks._framework.adapter_base import BenchmarkAdapter
from tests.benchmarks._framework.registry import AdapterFactory


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    """Snapshot + restore registry state around each test.

    Cleans both ``_ADAPTER_FACTORIES`` and ``_REGISTRY_STATE`` so tests
    are independent and don't leak into the rest of the framework test
    suite that depends on the canonical bootstrap having run.
    """
    saved_factories = dict(registry._ADAPTER_FACTORIES)
    saved_state = dict(registry._REGISTRY_STATE)
    registry._ADAPTER_FACTORIES.clear()
    registry._REGISTRY_STATE["bootstrapped"] = False
    try:
        yield
    finally:
        registry._ADAPTER_FACTORIES.clear()
        registry._ADAPTER_FACTORIES.update(saved_factories)
        registry._REGISTRY_STATE.clear()
        registry._REGISTRY_STATE.update(saved_state)


def _make_factory(label: str) -> AdapterFactory:
    """Identity-tagged sentinel factory — the registry never invokes it
    during these tests, so we don't need a real BenchmarkAdapter subclass."""

    def factory() -> BenchmarkAdapter:
        return cast(BenchmarkAdapter, object())

    factory.__name__ = f"factory_{label}"
    return factory


# --------------------------------------------------------------------------- #
# register_adapter                                                            #
# --------------------------------------------------------------------------- #


def test_register_adapter_is_idempotent_on_same_factory() -> None:
    factory = _make_factory("a")
    registry.register_adapter("foo", factory)
    registry.register_adapter("foo", factory)  # second call is a no-op
    assert registry._ADAPTER_FACTORIES["foo"] is factory


def test_register_adapter_rejects_duplicate_name_with_different_factory() -> None:
    registry.register_adapter("foo", _make_factory("a"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register_adapter("foo", _make_factory("b"))


# --------------------------------------------------------------------------- #
# ensure_known_adapters_registered                                            #
# --------------------------------------------------------------------------- #


def test_ensure_known_adapters_registered_only_runs_once() -> None:
    """Sentinel must prevent re-importing canonical adapters on later calls."""
    with patch("importlib.import_module") as mocked:
        registry.ensure_known_adapters_registered()
        count_after_first = mocked.call_count
        registry.ensure_known_adapters_registered()
        registry.ensure_known_adapters_registered()
        count_after_three = mocked.call_count
    assert count_after_first >= 1, "first call must attempt the canonical imports"
    assert count_after_three == count_after_first, (
        "subsequent calls must short-circuit on the sentinel"
    )


def test_ensure_known_adapters_registered_runs_when_other_adapters_pre_registered() -> None:
    """Regression for the ``known_adapters()`` sentinel bug.

    Previous code checked ``if known_adapters(): return``, so pre-registering
    a mock adapter would silently skip the canonical bootstrap. The new
    ``_REGISTRY_STATE["bootstrapped"]`` flag decouples "have we tried?"
    from "is the dict non-empty?".
    """
    registry.register_adapter("mock", _make_factory("mock"))
    with patch("importlib.import_module") as mocked:
        registry.ensure_known_adapters_registered()
    assert mocked.call_count >= 1, (
        "bootstrap must run even with a pre-registered adapter — otherwise "
        "tests / hooks could silently disable the canonical adapter set"
    )


def test_ensure_known_adapters_registered_marks_bootstrapped_before_loop() -> None:
    """Setting the sentinel before the loop means a partial failure on one
    adapter does NOT trigger a retry next call — bootstrap is once per process."""

    def _raise(_: str) -> None:
        raise ImportError("simulated missing dep")

    with patch("importlib.import_module", side_effect=_raise):
        registry.ensure_known_adapters_registered()
    assert registry._REGISTRY_STATE["bootstrapped"] is True


# --------------------------------------------------------------------------- #
# build_adapter                                                               #
# --------------------------------------------------------------------------- #


def test_build_adapter_unknown_name_lists_known_adapters() -> None:
    registry._REGISTRY_STATE["bootstrapped"] = True  # skip canonical import for isolation
    registry.register_adapter("foo", _make_factory("a"))
    with pytest.raises(KeyError) as exc_info:
        registry.build_adapter("nonexistent")
    message = str(exc_info.value)
    assert "nonexistent" in message
    assert "known adapters" in message
    assert "foo" in message


def test_build_adapter_empty_registry_shows_fallback_message() -> None:
    registry._REGISTRY_STATE["bootstrapped"] = True  # skip canonical import; keep dict empty
    with pytest.raises(KeyError) as exc_info:
        registry.build_adapter("anything")
    assert "<none registered>" in str(exc_info.value)


def test_build_adapter_returns_factory_result() -> None:
    sentinel = cast(BenchmarkAdapter, object())

    def factory() -> BenchmarkAdapter:
        return sentinel

    registry._REGISTRY_STATE["bootstrapped"] = True
    registry.register_adapter("foo", factory)
    assert registry.build_adapter("foo") is sentinel
