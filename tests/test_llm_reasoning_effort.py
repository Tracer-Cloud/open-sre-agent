"""Unit tests for session-scoped reasoning effort helpers."""

from __future__ import annotations

import os
import threading

import pytest

from app.llm_reasoning_effort import (
    ReasoningEffortChoice,
    apply_reasoning_effort,
    describe_reasoning_effort_default,
    display_reasoning_effort,
    get_active_reasoning_effort,
    infer_reasoning_effort_default,
)

_ENV_KEY = "OPENSRE_REASONING_EFFORT"


@pytest.fixture(autouse=True)
def _clear_reasoning_effort_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV_KEY, raising=False)


def test_apply_none_preserves_shell_env_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV_KEY, "high")

    with apply_reasoning_effort(None):
        assert get_active_reasoning_effort() == "high"
        assert _ENV_KEY in os.environ

    assert os.environ.get(_ENV_KEY) == "high"


def test_apply_non_none_overrides_env_until_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV_KEY, "low")

    with apply_reasoning_effort("high"):
        assert get_active_reasoning_effort() == "high"

    assert get_active_reasoning_effort() == "low"


def test_concurrent_threads_do_not_cross_session_overrides() -> None:
    """Session overrides use contextvars (per-thread), not shared os.environ."""
    mid = threading.Barrier(2)
    observed: dict[int, str | None] = {}

    def worker(tid: int, effort: ReasoningEffortChoice) -> None:
        with apply_reasoning_effort(effort):
            mid.wait()
            observed[tid] = get_active_reasoning_effort()

    threads = (
        threading.Thread(target=worker, args=(1, "high")),
        threading.Thread(target=worker, args=(2, "low")),
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert observed[1] == "high"
    assert observed[2] == "low"


def test_display_reasoning_effort_formats_default_in_parentheses() -> None:
    assert display_reasoning_effort(None) == "(default)"


def test_infer_reasoning_effort_default_for_openai_gpt_5_2() -> None:
    assert infer_reasoning_effort_default("openai", "gpt-5.2") == "none"


def test_describe_reasoning_effort_default_for_unsupported_provider() -> None:
    assert (
        describe_reasoning_effort_default("anthropic", "claude-opus-4-7")
        == "anthropic does not use reasoning-effort overrides"
    )
