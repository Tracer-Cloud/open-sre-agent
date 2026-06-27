"""Tests for interactive-shell session token accounting."""

from __future__ import annotations

import io
import time
from collections.abc import Iterator
from typing import Any

from rich.console import Console

from context.session import ReplSession
from interactive_shell.agent_shell.agent import answer_cli_agent
from interactive_shell.runtime.core.token_accounting import (
    build_llm_run_info,
    estimate_tokens,
    format_token_total,
    record_llm_turn,
)
from interactive_shell.ui.streaming import _CHARS_PER_TOKEN


def test_estimate_tokens_uses_chars_per_token_ratio() -> None:
    text = "x" * (8 * _CHARS_PER_TOKEN)
    assert estimate_tokens(text) == 8


def test_record_token_usage_accumulates_on_session() -> None:
    session = ReplSession()
    record_llm_turn(session, prompt="a" * 40, response="b" * 20)
    assert session.token_usage == {
        "input": 10,
        "output": 5,
        "input_estimated": 10,
        "output_estimated": 5,
    }
    assert session.llm_call_count == 1
    assert session.token_usage_has_estimates is True
    record_llm_turn(session, prompt="c" * 8, response="d" * 4)
    assert session.token_usage == {
        "input": 12,
        "output": 6,
        "input_estimated": 12,
        "output_estimated": 6,
    }
    assert session.llm_call_count == 2


def test_record_llm_turn_uses_provider_counts_when_present() -> None:
    session = ReplSession()
    inp, out, estimated = record_llm_turn(
        session,
        prompt="ignored when counts supplied",
        response="also ignored",
        input_tokens=1200,
        output_tokens=80,
    )
    assert (inp, out, estimated) == (1200, 80, False)
    assert session.token_usage == {
        "input": 1200,
        "output": 80,
        "input_measured": 1200,
        "output_measured": 80,
    }
    assert session.token_usage_has_estimates is False


def test_format_token_total_shows_mixed_breakdown() -> None:
    session = ReplSession()
    record_llm_turn(
        session,
        prompt="x",
        response="y",
        input_tokens=300,
        output_tokens=40,
    )
    record_llm_turn(session, prompt="a" * 400, response="b" * 80)
    assert format_token_total(session, direction="input") == (
        "input tokens",
        "400 (300 provider + 100 est.)",
    )
    assert format_token_total(session, direction="output") == (
        "output tokens",
        "60 (40 provider + 20 est.)",
    )


def test_build_llm_run_info_records_tokens_and_metadata() -> None:
    session = ReplSession()

    class _Client:
        _model = "claude-test"
        _provider_label = "Anthropic"

    run = build_llm_run_info(
        session=session,
        prompt="a" * 40,
        response_text="b" * 20,
        client=_Client(),
        started=time.monotonic() - 0.05,
    )
    assert run.model == "claude-test"
    assert run.provider == "anthropic"
    assert run.input_tokens == 10
    assert run.output_tokens == 5
    assert run.response_text == "b" * 20
    assert run.latency_ms is not None and run.latency_ms >= 0


def test_coerce_usage_tokens_accepts_float_counts() -> None:
    from core.runtime.llm.llm_client import _coerce_usage_tokens

    assert _coerce_usage_tokens(
        {"input_tokens": 512.0, "output_tokens": 64.0},
        input_key="input_tokens",
        output_key="output_tokens",
    ) == (512, 64)


def test_record_token_usage_skips_zero_counts() -> None:
    session = ReplSession()
    session.record_token_usage()
    assert session.token_usage == {}
    assert session.llm_call_count == 0


class _FakeLLMClient:
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_prompt: str | None = None

    def invoke_stream(self, prompt: str) -> Iterator[str]:
        self.last_prompt = prompt
        yield self._content


def test_answer_cli_agent_records_session_token_usage(monkeypatch: Any) -> None:
    client = _FakeLLMClient("assistant reply")
    monkeypatch.setattr("core.runtime.llm.llm_client.get_llm_for_reasoning", lambda: client)
    session = ReplSession()
    console = Console(file=io.StringIO(), force_terminal=False)
    answer_cli_agent("hello", session, console)
    assert session.token_usage["input"] > 0
    assert session.token_usage["output"] == estimate_tokens("assistant reply")
    assert session.token_usage_has_estimates is True
