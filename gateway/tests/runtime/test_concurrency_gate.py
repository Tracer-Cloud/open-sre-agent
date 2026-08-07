"""Tests for the process-wide Gateway turn gate."""

from __future__ import annotations

import logging
import threading

import pytest

from gateway.core.runtime.concurrency import (
    ConcurrencyLimitedTurnHandler,
    TurnConcurrencyGate,
)
from gateway.core.runtime.scheduler_concurrency import gate_registered_scheduler_runners
from platform.deployment_contracts.models import SizeProfile
from platform.scheduler.agent_runner import (
    invoke_agent_runner,
    register_agent_runner,
)


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (SizeProfile.SMALL, 1),
        (SizeProfile.MEDIUM, 2),
        (SizeProfile.LARGE, 4),
    ],
)
def test_profile_limits(profile: SizeProfile, expected: int) -> None:
    gate = TurnConcurrencyGate.for_profile(profile)
    acquired = [gate.try_acquire() for _ in range(expected + 1)]

    assert acquired == [True] * expected + [False]


def test_chat_handler_releases_capacity_in_finally() -> None:
    gate = TurnConcurrencyGate(1)

    def failing_handler(*_args: object) -> None:
        raise RuntimeError("sensitive detail")

    handler = ConcurrencyLimitedTurnHandler(handler=failing_handler, gate=gate)

    with pytest.raises(RuntimeError, match="sensitive detail"):
        handler("hello", object(), object(), logging.getLogger("test"))  # type: ignore[arg-type]

    assert gate.try_acquire() is True
    gate.release()


def test_chat_handler_refuses_excess_turn_without_calling_handler() -> None:
    gate = TurnConcurrencyGate(1)
    entered = threading.Event()
    release = threading.Event()
    finalized: list[str] = []

    def blocking_handler(*_args: object) -> None:
        entered.set()
        release.wait(1)

    class Sink:
        def finalize(self, text: str) -> None:
            finalized.append(text)

    handler = ConcurrencyLimitedTurnHandler(handler=blocking_handler, gate=gate)
    first = threading.Thread(
        target=handler,
        args=("one", object(), Sink(), logging.getLogger("test")),
    )
    first.start()
    assert entered.wait(1)

    handler("two", object(), Sink(), logging.getLogger("test"))  # type: ignore[arg-type]
    release.set()
    first.join(1)

    assert finalized == ["OpenSRE is at capacity. Please try again shortly."]


def test_scheduler_runner_waits_for_the_same_chat_capacity() -> None:
    gate = TurnConcurrencyGate(1)
    assert gate.try_acquire() is True  # active chat turn
    entered = threading.Event()
    result: list[str] = []

    def scheduled_runner(_payload: dict[str, object]) -> str:
        entered.set()
        return "done"

    register_agent_runner(scheduled_runner)
    gate_registered_scheduler_runners(gate)
    thread = threading.Thread(
        target=lambda: result.append(invoke_agent_runner({})),
    )
    thread.start()
    assert not entered.wait(0.05)

    gate.release()
    assert entered.wait(1)
    thread.join(1)
    register_agent_runner(None)

    assert result == ["done"]
