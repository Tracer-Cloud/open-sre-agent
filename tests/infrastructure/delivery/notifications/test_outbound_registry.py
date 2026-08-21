"""Contract tests for the outbound notification registry and dispatcher."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from infrastructure.delivery.notifications.outbound_dispatch import (
    dispatch_background_notifications,
)
from infrastructure.delivery.notifications.outbound_registry import (
    BACKGROUND_RCA,
    clear_outbound_adapters,
    get_outbound_adapter,
    outbound_adapter_names_for,
    register_outbound_adapter,
    registered_outbound_adapter_names,
)
from infrastructure.scheduling.background_investigations.types import BackgroundInvestigationRecord


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    """Snapshot and restore the registry around every test.

    The registry is process-global module state. A test that clears it without
    restoring would leave every later test in the same xdist worker resolving
    no adapter at all, which reports "unsupported" rather than failing loudly.
    """
    saved = {name: get_outbound_adapter(name) for name in registered_outbound_adapter_names()}
    clear_outbound_adapters()
    yield
    clear_outbound_adapters()
    for adapter in saved.values():
        if adapter is not None:
            register_outbound_adapter(adapter)


class _StubAdapter:
    def __init__(self, name: str, outcome: str = "sent", *, capable: bool = True) -> None:
        self.name = name
        self.capabilities = frozenset({BACKGROUND_RCA}) if capable else frozenset()
        self._outcome = outcome
        self.calls: list[str] = []

    def deliver(self, record: BackgroundInvestigationRecord) -> str:
        self.calls.append(record.task_id)
        return self._outcome


class _RaisingAdapter:
    name = "boom"
    capabilities = frozenset({BACKGROUND_RCA})

    def deliver(self, record: BackgroundInvestigationRecord) -> str:
        _ = record
        raise RuntimeError("transport exploded")


def _record() -> BackgroundInvestigationRecord:
    return BackgroundInvestigationRecord(
        task_id="bg-1", status="completed", command="/investigate x", root_cause="boom"
    )


def test_register_then_get_returns_the_same_adapter() -> None:
    adapter = _StubAdapter("telegram")
    register_outbound_adapter(adapter)

    assert get_outbound_adapter("telegram") is adapter
    assert registered_outbound_adapter_names() == ("telegram",)


def test_registration_is_idempotent_and_last_wins() -> None:
    """Tests inject stubs over real adapters; the second registration must win."""
    register_outbound_adapter(_StubAdapter("telegram", "sent"))
    replacement = _StubAdapter("telegram", "failed: replaced")
    register_outbound_adapter(replacement)

    assert get_outbound_adapter("telegram") is replacement
    assert registered_outbound_adapter_names() == ("telegram",)


def test_get_returns_none_for_an_unregistered_channel() -> None:
    assert get_outbound_adapter("nope") is None


def test_capability_filter_reports_sorted_names_and_excludes_non_capable() -> None:
    register_outbound_adapter(_StubAdapter("telegram"))
    register_outbound_adapter(_StubAdapter("email"))
    register_outbound_adapter(_StubAdapter("silent", capable=False))

    assert outbound_adapter_names_for(BACKGROUND_RCA) == ("email", "telegram")


def test_clear_empties_the_registry() -> None:
    register_outbound_adapter(_StubAdapter("telegram"))
    clear_outbound_adapters()

    assert registered_outbound_adapter_names() == ()


def test_dispatch_reports_unsupported_for_an_unknown_channel() -> None:
    assert dispatch_background_notifications(record=_record(), channels=("nope",)) == {
        "nope": "unsupported"
    }


def test_dispatch_reports_unsupported_when_the_adapter_lacks_the_capability() -> None:
    """A registered adapter that does not advertise background RCA is not usable
    for it, and must read the same as an unknown channel rather than deliver."""
    adapter = _StubAdapter("silent", capable=False)
    register_outbound_adapter(adapter)

    results = dispatch_background_notifications(record=_record(), channels=("silent",))

    assert results == {"silent": "unsupported"}
    assert adapter.calls == []


def test_dispatch_follows_the_caller_channel_order_not_registration_order() -> None:
    """`/background show` renders the mapping in insertion order, and dict
    equality cannot catch a reorder, so assert the key sequence directly."""
    register_outbound_adapter(_StubAdapter("telegram"))
    register_outbound_adapter(_StubAdapter("email"))

    forward = dispatch_background_notifications(record=_record(), channels=("email", "telegram"))
    reverse = dispatch_background_notifications(record=_record(), channels=("telegram", "email"))

    assert list(forward) == ["email", "telegram"]
    assert list(reverse) == ["telegram", "email"]


def test_dispatch_returns_empty_for_no_channels() -> None:
    register_outbound_adapter(_StubAdapter("telegram"))

    assert dispatch_background_notifications(record=_record(), channels=()) == {}


def test_dispatch_records_an_adapter_exception_and_keeps_going() -> None:
    """A raising channel is one channel's outcome, not the whole dispatch.

    Propagating lost every later channel and reached the runner's failure
    handler, which rewrote an already completed RCA to ``failed``. The outcome
    string is what ``/background show`` renders, so the user still learns the
    channel failed.
    """
    register_outbound_adapter(_RaisingAdapter())
    delivered = _StubAdapter("telegram")
    register_outbound_adapter(delivered)

    results = dispatch_background_notifications(record=_record(), channels=("boom", "telegram"))

    assert results == {"boom": "failed: RuntimeError", "telegram": "sent"}
    assert delivered.calls == ["bg-1"]
