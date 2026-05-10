"""Tests for app/agents/error_signals.py."""

from __future__ import annotations

import re
import threading

import pytest

from app.agents.error_signals import (
    DEFAULT_CATEGORIES,
    ErrorCategory,
    ErrorSignals,
)


class _FakeClock:
    """Deterministic clock for sliding-window tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _signals_with_clock(
    clock: _FakeClock,
    *,
    window_seconds: float = 60.0,
    categories: tuple[ErrorCategory, ...] | None = None,
) -> ErrorSignals:
    return ErrorSignals(
        categories=categories,
        window_seconds=window_seconds,
        now=clock,
    )


# ---------------------------------------------------------------------------
# Initialization / contract
# ---------------------------------------------------------------------------


def test_initial_rates_are_all_zero() -> None:
    signals = ErrorSignals()

    rates = signals.rate_per_minute()

    assert rates == {cat.name: 0.0 for cat in DEFAULT_CATEGORIES}


def test_observe_empty_chunk_is_a_noop() -> None:
    signals = ErrorSignals()

    signals.observe("")

    assert signals.rate_per_minute() == {cat.name: 0.0 for cat in DEFAULT_CATEGORIES}


def test_invalid_window_raises() -> None:
    with pytest.raises(ValueError, match="window_seconds must be > 0"):
        ErrorSignals(window_seconds=0)
    with pytest.raises(ValueError, match="window_seconds must be > 0"):
        ErrorSignals(window_seconds=-1.0)


def test_duplicate_category_names_raise() -> None:
    with pytest.raises(ValueError, match="duplicate category name"):
        ErrorSignals(
            categories=(
                ErrorCategory(name="dup", keywords=("a",)),
                ErrorCategory(name="dup", keywords=("b",)),
            ),
        )


# ---------------------------------------------------------------------------
# False-positive guards (acceptance criterion: no false positives on neutral text)
# ---------------------------------------------------------------------------


def test_no_false_positive_on_simple_neutral_prose() -> None:
    signals = ErrorSignals()

    signals.observe(
        "Hello, the meeting is at 5pm. Phone is 555-1234. "
        "I love programming and the price is $599. Show me the file at line 502."
    )

    assert signals.rate_per_minute() == {cat.name: 0.0 for cat in DEFAULT_CATEGORIES}


def test_no_false_positive_on_descriptive_rate_limit_mention() -> None:
    """Plain prose mentioning rate limits descriptively must not fire."""
    signals = ErrorSignals()

    signals.observe("we should rate limit the API to 10 req/sec")
    signals.observe("the OpenAI rate limit is 10k tokens per minute")
    signals.observe("no rate limit was hit")  # negation
    signals.observe("rate limit configuration is in the env file")

    assert signals.rate_per_minute()["rate_limit"] == 0.0


def test_no_false_positive_on_tool_error_handling_phrase() -> None:
    """Code-suggestion text containing 'tool error' or 'tool failure' must not fire."""
    signals = ErrorSignals()

    signals.observe("add tool error handling around the call")
    signals.observe("fixed a tool error in last commit")
    signals.observe("tool failure modes are covered in the docs")
    signals.observe("we discussed tool failure recovery yesterday")

    assert signals.rate_per_minute()["tool_failure"] == 0.0


def test_no_false_positive_on_traceback_word_in_prose() -> None:
    """Bare 'Traceback' in instructive text must not fire."""
    signals = ErrorSignals()

    signals.observe("show me the Traceback for that bug")
    signals.observe("Traceback in Python is helpful for debugging")
    signals.observe("read the Traceback carefully")

    assert signals.rate_per_minute()["traceback"] == 0.0


def test_no_false_positive_on_json_log_line_with_status_field() -> None:
    """A JSON log entry with a 'status' field must not trip http_5xx."""
    signals = ErrorSignals()

    signals.observe('{"level": "info", "status": 200, "msg": "ok"}')
    signals.observe('{"endpoint": "/v1/users", "status_code": 201, "latency_ms": 502}')

    assert signals.rate_per_minute()["http_5xx"] == 0.0


def test_no_false_positive_on_markdown_explainer() -> None:
    """A markdown paragraph descriptively covering all four categories must not fire."""
    signals = ErrorSignals()

    signals.observe(
        "## Common errors\n"
        "When you hit the rate limit you should back off. "
        "If a tool failure pattern shows up, retry with exponential delay. "
        "A Python Traceback is your friend during debugging. "
        "HTTP 5xx responses indicate the server is having a bad time."
    )

    assert signals.rate_per_minute() == {cat.name: 0.0 for cat in DEFAULT_CATEGORIES}


def test_no_false_positive_on_bare_three_digit_numbers() -> None:
    """Plain numbers like '500 lines of code' must not trip the 5xx counter."""
    signals = ErrorSignals()

    signals.observe("There are 500 lines of code, file size is 502KB, line 599 has a typo.")

    assert signals.rate_per_minute()["http_5xx"] == 0.0


# ---------------------------------------------------------------------------
# Positive detection (rate_limit)
# ---------------------------------------------------------------------------


def test_detects_rate_limit_exceeded() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("Anthropic API: rate limit exceeded, retrying in 5s")

    rates = signals.rate_per_minute()
    assert rates["rate_limit"] == 1.0
    assert rates["http_5xx"] == 0.0
    assert rates["tool_failure"] == 0.0
    assert rates["traceback"] == 0.0


def test_detects_rate_limit_hit_or_reached() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("rate limit hit")
    signals.observe("rate-limit reached")
    signals.observe("ratelimited")

    assert signals.rate_per_minute()["rate_limit"] == 3.0


def test_detects_429_with_rate_limit_context() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("got 429: too many requests")
    signals.observe("HTTP 429 - rate limit")

    assert signals.rate_per_minute()["rate_limit"] == 2.0


def test_rate_limit_is_case_insensitive() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("RATE LIMIT EXCEEDED")
    signals.observe("Rate-Limit Hit")

    assert signals.rate_per_minute()["rate_limit"] == 2.0


# ---------------------------------------------------------------------------
# Positive detection (http_5xx)
# ---------------------------------------------------------------------------


def test_detects_http_5xx_with_status_context() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("HTTP/1.1 503 Service Unavailable")
    signals.observe("status: 500 internal server error")
    signals.observe("got 502 Bad Gateway from upstream")

    assert signals.rate_per_minute()["http_5xx"] == 3.0


def test_detects_http_5xx_case_insensitive() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("http/1.1 503 service unavailable")
    signals.observe("STATUS: 500")

    assert signals.rate_per_minute()["http_5xx"] == 2.0


# ---------------------------------------------------------------------------
# Positive detection (tool_failure)
# ---------------------------------------------------------------------------


def test_detects_tool_failure_header_form() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("tool failure: bash exited with code 1")
    signals.observe("tool_failure: timeout")
    signals.observe("tool failure - subprocess died")

    assert signals.rate_per_minute()["tool_failure"] == 3.0


def test_detects_tool_failed_with_action_verb() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("the tool failed during execution")
    signals.observe("tool failed with code 137")
    signals.observe("tool failed because of bad input")

    assert signals.rate_per_minute()["tool_failure"] == 3.0


def test_detects_tool_exited_with_code() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("tool exited with code 2")
    signals.observe("tool exited with 1")

    assert signals.rate_per_minute()["tool_failure"] == 2.0


# ---------------------------------------------------------------------------
# Positive detection (traceback)
# ---------------------------------------------------------------------------


def test_detects_python_traceback_header() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe('Traceback (most recent call last):\n  File "x.py", line 1\nValueError: bad')

    assert signals.rate_per_minute()["traceback"] == 1.0


# ---------------------------------------------------------------------------
# Combinatorics / counting
# ---------------------------------------------------------------------------


def test_one_chunk_can_match_multiple_categories() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe(
        "rate limit exceeded; server returned status: 503; tool failure: subprocess died"
    )

    rates = signals.rate_per_minute()
    assert rates["rate_limit"] == 1.0
    assert rates["http_5xx"] == 1.0
    assert rates["tool_failure"] == 1.0


def test_multiple_occurrences_in_one_chunk_count_separately() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("rate limit exceeded ... rate limit exceeded ... rate limit exceeded")

    assert signals.rate_per_minute()["rate_limit"] == 3.0


# ---------------------------------------------------------------------------
# Sliding window
# ---------------------------------------------------------------------------


def test_sliding_window_prunes_old_events() -> None:
    """Events older than window_seconds must drop out of the rate."""
    clock = _FakeClock(start=0.0)
    signals = _signals_with_clock(clock, window_seconds=60.0)

    # t=0: three rate-limit events
    signals.observe("rate limit exceeded")
    signals.observe("rate limit exceeded")
    signals.observe("rate limit exceeded")
    assert signals.rate_per_minute()["rate_limit"] == 3.0

    # t=30: still inside window
    clock.advance(30.0)
    assert signals.rate_per_minute()["rate_limit"] == 3.0

    # t=61: all three originals just fell out
    clock.advance(31.0)
    assert signals.rate_per_minute()["rate_limit"] == 0.0


def test_rate_normalizes_to_per_minute_for_short_window() -> None:
    """A 30s window with 2 events reports as 4.0/min, not 2.0/min."""
    clock = _FakeClock(start=0.0)
    signals = _signals_with_clock(clock, window_seconds=30.0)

    signals.observe("rate limit exceeded")
    signals.observe("rate limit exceeded")

    assert signals.rate_per_minute()["rate_limit"] == 4.0


def test_categories_are_independent_in_window() -> None:
    """Pruning one category must not affect another."""
    clock = _FakeClock(start=0.0)
    signals = _signals_with_clock(clock, window_seconds=60.0)

    signals.observe("rate limit exceeded")  # t=0
    clock.advance(30.0)
    signals.observe("Traceback (most recent call last):")  # t=30

    clock.advance(31.0)  # t=61, rate_limit dropped, traceback still in
    rates = signals.rate_per_minute()
    assert rates["rate_limit"] == 0.0
    assert rates["traceback"] == 1.0


# ---------------------------------------------------------------------------
# Memory bounds (P2-1)
# ---------------------------------------------------------------------------


def test_observe_prunes_expired_events_so_idle_dashboard_does_not_grow_unbounded() -> None:
    """If rate_per_minute() is never called, observe() must still keep memory bounded."""
    clock = _FakeClock(start=0.0)
    signals = _signals_with_clock(clock, window_seconds=60.0)

    # Hammer 1000 events at t=0
    for _ in range(1000):
        signals.observe("rate limit exceeded")

    # Advance past the window and observe one more (no rate_per_minute call yet)
    clock.advance(120.0)
    signals.observe("rate limit exceeded")

    # The deque must have been pruned during observe(), not just on query.
    rate = signals.rate_per_minute()["rate_limit"]
    assert rate == 1.0


# ---------------------------------------------------------------------------
# Custom categories
# ---------------------------------------------------------------------------


def test_custom_categories_override_defaults() -> None:
    clock = _FakeClock()
    custom = (
        ErrorCategory(
            name="oom",
            keywords=("OutOfMemoryError", "killed by oom"),
            patterns=(re.compile(r"signal:\s*9", re.IGNORECASE),),
        ),
    )
    signals = _signals_with_clock(clock, categories=custom)

    signals.observe("Process killed by OOM at 12:34")
    signals.observe("subprocess died: signal: 9")

    rates = signals.rate_per_minute()
    assert rates == {"oom": 2.0}


def test_keyword_matching_is_word_bounded() -> None:
    """A keyword like 'error' must match the word 'error' but not 'errored',
    'errorless', or 'noerror'. Substring matching would let an adversarial
    agent inflate the counter and trigger false SLO breaches."""
    clock = _FakeClock()
    signals = _signals_with_clock(
        clock,
        categories=(ErrorCategory(name="custom", keywords=("error",)),),
    )

    signals.observe("the call errored at line 42")
    signals.observe("an errorless run")
    signals.observe("the noerror flag is set")
    assert signals.rate_per_minute()["custom"] == 0.0

    signals.observe("an error occurred")
    signals.observe("ERROR: bad input")
    assert signals.rate_per_minute()["custom"] == 2.0


def test_empty_keyword_strings_are_ignored() -> None:
    """An empty-string keyword would match every chunk; defend against it."""
    clock = _FakeClock()
    signals = _signals_with_clock(
        clock,
        categories=(ErrorCategory(name="bad", keywords=("",)),),
    )

    signals.observe("totally normal text without any errors")

    assert signals.rate_per_minute() == {"bad": 0.0}


def test_non_ascii_chunks_do_not_crash() -> None:
    clock = _FakeClock()
    signals = _signals_with_clock(clock)

    signals.observe("こんにちは 你好 مرحبا running smoothly")

    assert all(v == 0.0 for v in signals.rate_per_minute().values())


# ---------------------------------------------------------------------------
# Thread safety (P2-2)
# ---------------------------------------------------------------------------


def test_concurrent_observe_and_query_does_not_raise() -> None:
    """Tail thread observes while renderer thread queries; no crashes, sane bounds."""
    signals = ErrorSignals(window_seconds=60.0)
    stop = threading.Event()
    errors: list[Exception] = []

    def writer() -> None:
        try:
            for _ in range(5000):
                signals.observe("rate limit exceeded")
        except Exception as e:
            errors.append(e)
        finally:
            stop.set()

    def reader() -> None:
        try:
            while not stop.is_set():
                rates = signals.rate_per_minute()
                assert rates["rate_limit"] >= 0.0
        except Exception as e:
            errors.append(e)

    t_writer = threading.Thread(target=writer)
    t_reader = threading.Thread(target=reader)
    t_writer.start()
    t_reader.start()
    t_writer.join(timeout=10.0)
    t_reader.join(timeout=10.0)

    assert not errors
    final = signals.rate_per_minute()
    # All 5000 events should normally complete well under the 60s window, but
    # tolerate a slow CI runner where a small fraction may have expired by the
    # time we read the rate. The point of the test is no exceptions; the
    # bound just guards against silent total loss.
    assert final["rate_limit"] >= 4500.0


def test_concurrent_pruning_during_active_expiry_does_not_raise() -> None:
    """Both methods prune the same deque; with events actively expiring, the
    compound 'check then popleft' must not race into an IndexError."""
    # Tiny window means events expire essentially immediately, so both
    # observe() and rate_per_minute() will be racing on the same prune path.
    signals = ErrorSignals(window_seconds=0.001)
    stop = threading.Event()
    errors: list[Exception] = []

    def writer() -> None:
        try:
            for _ in range(2000):
                signals.observe("rate limit exceeded")
        except Exception as e:
            errors.append(e)
        finally:
            stop.set()

    def reader() -> None:
        try:
            while not stop.is_set():
                signals.rate_per_minute()
        except Exception as e:
            errors.append(e)

    t_writer = threading.Thread(target=writer)
    t_reader = threading.Thread(target=reader)
    t_writer.start()
    t_reader.start()
    t_writer.join(timeout=10.0)
    t_reader.join(timeout=10.0)

    assert not errors
