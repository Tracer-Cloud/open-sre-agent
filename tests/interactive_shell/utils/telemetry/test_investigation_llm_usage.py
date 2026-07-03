"""Tests for the investigation LLM usage observer."""

from __future__ import annotations

from core.llm.usage import emit_usage, set_usage_hook
from surfaces.interactive_shell.utils.telemetry.investigation_llm_usage import (
    observe_investigation_llm_usage,
)


def test_observe_accumulates_usage_and_clears_hook() -> None:
    with observe_investigation_llm_usage() as usage:
        emit_usage("claude-sonnet-4-5", 100, 20)
        emit_usage("claude-sonnet-4-5", 50, 10)
    assert usage.model == "claude-sonnet-4-5"
    assert usage.input_tokens == 150
    assert usage.output_tokens == 30
    assert usage.observed
    # Hook must be released so later owners can register.
    set_usage_hook(lambda *_args: None)
    set_usage_hook(None)


def test_observe_tolerates_already_registered_hook() -> None:
    external: list[tuple[str, int, int]] = []
    set_usage_hook(lambda model, inp, out: external.append((model, inp, out)))
    try:
        with observe_investigation_llm_usage() as usage:
            emit_usage("m", 10, 5)
        assert not usage.observed
        assert external == [("m", 10, 5)]
        # The pre-existing hook must survive the observer.
        emit_usage("m", 1, 1)
        assert len(external) == 2
    finally:
        set_usage_hook(None)


def test_observe_ignores_usage_outside_scope() -> None:
    with observe_investigation_llm_usage() as usage:
        pass
    emit_usage("m", 10, 5)
    assert not usage.observed
