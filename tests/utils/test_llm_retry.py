"""Tests for the shared LLM rate-limit retry helper."""

from __future__ import annotations

import pytest

from app.utils.llm_retry import (
    DEFAULT_MAX_ATTEMPTS,
    RETRY_AFTER_MAX_SEC,
    extract_retry_after_seconds,
    is_rate_limit_error,
    retry_on_rate_limit,
)

# --------------------------------------------------------------------------- #
# is_rate_limit_error — provider recognizer                                   #
# --------------------------------------------------------------------------- #


def test_is_rate_limit_error_recognizes_openai_wrapper() -> None:
    assert is_rate_limit_error(RuntimeError("OpenAI rate limit exceeded: 429"))
    assert is_rate_limit_error(
        RuntimeError(
            "Rate limit reached for gpt-4o-2024-11-20 ... "
            "tokens per min (TPM): Limit 30000, Used 29248."
        )
    )


def test_is_rate_limit_error_recognizes_anthropic_wrapper() -> None:
    assert is_rate_limit_error(RuntimeError("Anthropic rate limit exceeded: 429"))


def test_is_rate_limit_error_recognizes_structured_code() -> None:
    assert is_rate_limit_error(RuntimeError("provider error: rate_limit_exceeded"))


def test_is_rate_limit_error_case_insensitive() -> None:
    assert is_rate_limit_error(RuntimeError("RATE LIMIT EXCEEDED"))
    assert is_rate_limit_error(RuntimeError("TPM Exceeded"))


def test_is_rate_limit_error_does_not_match_unrelated_errors() -> None:
    # Don't retry 400 schema errors, generic exceptions, or model-not-found.
    assert not is_rate_limit_error(RuntimeError("Anthropic request rejected (HTTP 400)"))
    assert not is_rate_limit_error(ValueError("invalid input schema"))
    assert not is_rate_limit_error(RuntimeError("OpenAI model 'foo' not found"))


# --------------------------------------------------------------------------- #
# retry_on_rate_limit — control flow                                          #
# --------------------------------------------------------------------------- #


def test_retry_on_rate_limit_returns_immediately_on_success() -> None:
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        return "ok"

    assert retry_on_rate_limit(fn, label="test") == "ok"
    assert calls["n"] == 1


def test_retry_on_rate_limit_recovers_after_transient_failures(monkeypatch) -> None:
    """Two 429s, then success — final result returned, 3 total calls."""
    import app.utils.llm_retry as llm_retry

    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("OpenAI rate limit exceeded")
        return "ok"

    assert retry_on_rate_limit(fn, label="test") == "ok"
    assert calls["n"] == 3


def test_retry_on_rate_limit_reraises_after_exhausting(monkeypatch) -> None:
    """Always 429 — should re-raise after max_attempts. No silent None."""
    import app.utils.llm_retry as llm_retry

    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise RuntimeError("Anthropic rate limit exceeded")

    with pytest.raises(RuntimeError, match="rate limit"):
        retry_on_rate_limit(fn, label="test")
    assert calls["n"] == DEFAULT_MAX_ATTEMPTS


def test_retry_on_rate_limit_does_not_retry_non_rate_limit_errors(monkeypatch) -> None:
    """A schema error fails fast — retrying a deterministic bug is wasted."""
    import app.utils.llm_retry as llm_retry

    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise ValueError("invalid schema")

    with pytest.raises(ValueError, match="invalid schema"):
        retry_on_rate_limit(fn, label="test")
    # Exactly one attempt — no retry on non-rate-limit errors.
    assert calls["n"] == 1


def test_retry_on_rate_limit_respects_custom_max_attempts(monkeypatch) -> None:
    """Caller can lower / raise the retry count for their tolerance budget."""
    import app.utils.llm_retry as llm_retry

    monkeypatch.setattr(llm_retry.time, "sleep", lambda _s: None)

    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise RuntimeError("rate limit exceeded")

    with pytest.raises(RuntimeError):
        retry_on_rate_limit(fn, label="test", max_attempts=5)
    assert calls["n"] == 5


def test_retry_on_rate_limit_applies_full_jitter_with_doubling_upper_bound(
    monkeypatch,
) -> None:
    """random.uniform must be called with (0, current_backoff), and the
    upper bound must double on each retry — that's the exponential growth
    inside the jitter envelope."""
    import app.utils.llm_retry as llm_retry

    sleeps: list[float] = []
    jitter_bounds: list[tuple[float, float]] = []

    monkeypatch.setattr(llm_retry.time, "sleep", lambda s: sleeps.append(s))

    def fake_uniform(low: float, high: float) -> float:
        jitter_bounds.append((low, high))
        # Deterministic: pick the midpoint so the test asserts the sleep
        # value without needing to seed random.
        return (low + high) / 2

    monkeypatch.setattr(llm_retry.random, "uniform", fake_uniform)

    def fn() -> str:
        raise RuntimeError("rate limit exceeded")

    with pytest.raises(RuntimeError):
        retry_on_rate_limit(fn, label="test", initial_backoff_sec=2.0)

    # 3 attempts → 2 sleeps between them (no sleep after the final raise).
    # Full jitter envelope is [0, backoff], backoff doubles each retry.
    assert jitter_bounds == [(0.0, 2.0), (0.0, 4.0)]
    assert sleeps == [1.0, 2.0]  # midpoints from fake_uniform


# --------------------------------------------------------------------------- #
# extract_retry_after_seconds — provider hint parsing                         #
# --------------------------------------------------------------------------- #


class _FakeResponse:
    """Stand-in for httpx.Response with a ``.headers`` dict-like attribute."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def _err_with_response(headers: dict[str, str], msg: str = "rate limited") -> RuntimeError:
    err = RuntimeError(msg)
    err.response = _FakeResponse(headers)  # type: ignore[attr-defined]
    return err


def test_extract_retry_after_reads_integer_seconds_header() -> None:
    err = _err_with_response({"retry-after": "5"})
    assert extract_retry_after_seconds(err) == 5.0


def test_extract_retry_after_reads_fractional_seconds_header() -> None:
    err = _err_with_response({"retry-after": "0.094"})
    assert extract_retry_after_seconds(err) == pytest.approx(0.094)


def test_extract_retry_after_falls_back_to_body_text_milliseconds() -> None:
    # Real OpenAI 429 body text.
    err = RuntimeError(
        "OpenAI rate limit exceeded: Error code: 429 - "
        "Limit 30000, Used 29248, Requested 799. Please try again in 94ms."
    )
    assert extract_retry_after_seconds(err) == pytest.approx(0.094)


def test_extract_retry_after_falls_back_to_body_text_seconds() -> None:
    # 12s — below the RETRY_AFTER_MAX_SEC cap so we read the exact value.
    err = RuntimeError("rate limited: Please try again in 12s.")
    assert extract_retry_after_seconds(err) == 12.0


def test_extract_retry_after_caps_at_max() -> None:
    """A misconfigured provider returning ``retry-after: 3600`` must not
    hang the agent loop for an hour. Cap at RETRY_AFTER_MAX_SEC."""
    err = _err_with_response({"retry-after": "3600"})
    assert extract_retry_after_seconds(err) == RETRY_AFTER_MAX_SEC


def test_extract_retry_after_caps_body_hint_at_max() -> None:
    err = RuntimeError("rate limited: Please try again in 7200s.")
    assert extract_retry_after_seconds(err) == RETRY_AFTER_MAX_SEC


def test_extract_retry_after_returns_none_when_no_hint_present() -> None:
    # No response attached, no body hint — caller should fall back to jitter.
    assert extract_retry_after_seconds(RuntimeError("rate limited, generic")) is None
    # Response present but no retry-after header.
    err = _err_with_response({"x-other": "value"})
    assert extract_retry_after_seconds(err) is None


def test_extract_retry_after_skips_http_date_format() -> None:
    """HTTP-date Retry-After is allowed by RFC 7231 but rare. We don't parse
    it — fall through to body text or None."""
    err = _err_with_response({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
    assert extract_retry_after_seconds(err) is None


def test_extract_retry_after_ignores_negative_header() -> None:
    """Negative retry-after is invalid per spec; treat as no hint and fall
    back. (Some misconfigured proxies have been observed sending -1.)"""
    err = _err_with_response({"retry-after": "-1"})
    assert extract_retry_after_seconds(err) is None


def test_retry_on_rate_limit_jitter_is_non_deterministic_under_real_random() -> None:
    """Without monkeypatching random, repeated identical calls must NOT produce
    identical sleep durations — confirms random.uniform is actually wired
    into the production path (not e.g. accidentally short-circuited to a
    constant by a future refactor)."""
    import app.utils.llm_retry as llm_retry

    def fn() -> str:
        raise RuntimeError("rate limit exceeded")

    def capture_one_run() -> list[float]:
        sleeps: list[float] = []
        original_sleep = llm_retry.time.sleep
        llm_retry.time.sleep = sleeps.append  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError):
                retry_on_rate_limit(fn, label="test", initial_backoff_sec=2.0)
        finally:
            llm_retry.time.sleep = original_sleep  # type: ignore[assignment]
        return sleeps

    sleeps_across_runs = [capture_one_run() for _ in range(3)]

    # At least one pair of runs must differ — vanishingly unlikely for three
    # independent uniform draws to coincide on all values. If this is ever
    # flaky on real hardware, jitter is genuinely broken.
    assert any(
        sleeps_across_runs[i] != sleeps_across_runs[j]
        for i in range(len(sleeps_across_runs))
        for j in range(i + 1, len(sleeps_across_runs))
    ), "sleep durations identical across runs — jitter is not being applied"
