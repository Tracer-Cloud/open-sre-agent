"""Tests for the per-PID sliding-window token rate tracker (#2023)."""

from __future__ import annotations

import threading
import time

import pytest

from app.fleet_monitoring.meters import TokenUsage
from app.fleet_monitoring.token_rate import TokenRateTracker


@pytest.fixture
def tracker() -> TokenRateTracker:
    """Fresh tracker per test so module-level singleton state never leaks."""
    return TokenRateTracker()


class TestTokensPerMin:
    def test_unknown_pid_returns_none(self, tracker: TokenRateTracker) -> None:
        # Distinct from "known but idle". ``None`` lets the dashboard
        # render ``-`` instead of ``0`` for PIDs that were never
        # observed (e.g. claude-code agent on a TTY whose JSONL was
        # never resolved).
        assert tracker.tokens_per_min(9999) is None

    def test_known_idle_pid_returns_zero(self, tracker: TokenRateTracker) -> None:
        tracker.record(1234, tokens=0)
        # PID has been recorded — even with zero — so the dashboard
        # should distinguish it from never-seen.
        assert tracker.tokens_per_min(1234) == pytest.approx(0.0)

    def test_single_recent_record(self, tracker: TokenRateTracker) -> None:
        tracker.record(1234, tokens=150)
        assert tracker.tokens_per_min(1234) == pytest.approx(150.0)

    def test_multiple_records_in_window_sum(self, tracker: TokenRateTracker) -> None:
        tracker.record(1234, tokens=100)
        tracker.record(1234, tokens=200)
        tracker.record(1234, tokens=50)
        assert tracker.tokens_per_min(1234) == pytest.approx(350.0)

    def test_negative_tokens_clamped_to_zero(self, tracker: TokenRateTracker) -> None:
        # Defensive: a misbehaving meter should not bring the cell
        # negative. Clamp at insertion so the window math stays sane.
        tracker.record(1234, tokens=-10)
        tracker.record(1234, tokens=100)
        assert tracker.tokens_per_min(1234) == pytest.approx(100.0)

    def test_structured_usage_visible_total(self, tracker: TokenRateTracker) -> None:
        tracker.record(
            1234,
            usage=TokenUsage(
                input_tokens=100,
                output_tokens=50,
                cached_input_tokens=90,
                cache_read_input_tokens=200,
                cache_creation_input_tokens=25,
            ),
        )
        assert tracker.tokens_per_min(1234) == pytest.approx(375.0)

    def test_usage_per_min_returns_bucket_totals(self, tracker: TokenRateTracker) -> None:
        tracker.record(1234, usage=TokenUsage(input_tokens=100, cached_input_tokens=25))
        tracker.record(
            1234,
            usage=TokenUsage(
                output_tokens=50,
                cache_read_input_tokens=200,
                cache_creation_input_tokens=10,
            ),
        )
        usage = tracker.usage_per_min(1234)
        assert usage is not None
        assert usage.input_tokens == pytest.approx(100.0)
        assert usage.cached_input_tokens == pytest.approx(25.0)
        assert usage.output_tokens == pytest.approx(50.0)
        assert usage.cache_read_input_tokens == pytest.approx(200.0)
        assert usage.cache_creation_input_tokens == pytest.approx(10.0)


class TestWindowEviction:
    def test_old_entries_are_evicted(
        self, tracker: TokenRateTracker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Shrink the window to 1s so we can prove eviction without
        # racing wall-clock time. Class-level constant + class attr
        # patching keeps the lock behavior intact.
        monkeypatch.setattr(TokenRateTracker, "WINDOW_SECONDS", 1.0)

        tracker.record(1234, tokens=100)
        time.sleep(1.2)
        tracker.record(1234, tokens=50)

        # 100-token entry aged out, 50-token entry remains. Result is
        # scaled by the 60/WINDOW factor, so 50 × 60 = 3000.
        assert tracker.tokens_per_min(1234) == pytest.approx(3000.0)

    def test_all_entries_aged_out_returns_zero_not_none(
        self, tracker: TokenRateTracker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Once a PID has been recorded, it stays known forever (until
        # ``forget`` is called) — even when every entry has aged out.
        monkeypatch.setattr(TokenRateTracker, "WINDOW_SECONDS", 0.5)
        tracker.record(1234, tokens=100)
        time.sleep(0.7)
        assert tracker.tokens_per_min(1234) == pytest.approx(0.0)


class TestLatestModel:
    def test_unknown_pid_returns_none(self, tracker: TokenRateTracker) -> None:
        assert tracker.latest_model(9999) is None

    def test_returns_most_recent_non_none_model(self, tracker: TokenRateTracker) -> None:
        tracker.record(1234, tokens=10, model="claude-sonnet-4-5")
        tracker.record(1234, tokens=20, model="claude-opus-4-1")
        # Pricing should follow the active model — the most recent
        # one observed. A mid-session ``/model`` swap should be
        # reflected immediately.
        assert tracker.latest_model(1234) == "claude-opus-4-1"

    def test_skips_none_model_entries(self, tracker: TokenRateTracker) -> None:
        tracker.record(1234, tokens=10, model="claude-sonnet-4-5")
        # Subsequent record without a model hint must not erase the
        # previously known one — chunks that contain no assistant
        # event (system init, plain prose, etc.) still need to apply
        # a known price.
        tracker.record(1234, tokens=5, model=None)
        assert tracker.latest_model(1234) == "claude-sonnet-4-5"

    def test_returns_none_when_no_entry_carries_model(self, tracker: TokenRateTracker) -> None:
        tracker.record(1234, tokens=10, model=None)
        assert tracker.latest_model(1234) is None

    def test_survives_window_eviction(
        self, tracker: TokenRateTracker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A session that emitted a model once but then went idle for
        # > 60 s loses every token entry to the rolling window. The
        # model identity must persist so pricing keeps working.
        monkeypatch.setattr(TokenRateTracker, "WINDOW_SECONDS", 0.3)
        tracker.record(1234, tokens=10, model="claude-sonnet-4-5")
        time.sleep(0.5)
        assert tracker.tokens_per_min(1234) == pytest.approx(0.0)
        assert tracker.latest_model(1234) == "claude-sonnet-4-5"

    def test_codex_model_hint_survives_later_usage_after_window_eviction(
        self, tracker: TokenRateTracker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Codex emits the model in ``turn_context`` at turn start, but
        # the later ``token_count`` event has usage without a model.
        monkeypatch.setattr(TokenRateTracker, "WINDOW_SECONDS", 0.3)
        tracker.record(1234, tokens=0, model="gpt-5-codex")
        time.sleep(0.5)
        tracker.record(1234, tokens=225, model=None)

        assert tracker.tokens_per_min(1234) == pytest.approx(45000.0)
        assert tracker.latest_model(1234) == "gpt-5-codex"


class TestForgetAndKnownPids:
    def test_forget_removes_pid(self, tracker: TokenRateTracker) -> None:
        tracker.record(1234, tokens=100)
        tracker.forget(1234)
        # After forget, the PID is fully unknown — distinct from idle.
        assert tracker.tokens_per_min(1234) is None
        assert tracker.latest_model(1234) is None

    def test_forget_unknown_pid_is_silent(self, tracker: TokenRateTracker) -> None:
        # Sampler GC calls ``forget`` defensively on every disappeared
        # PID; it must not raise for PIDs the tracker never saw.
        tracker.forget(9999)

    def test_known_pids_lists_recorded(self, tracker: TokenRateTracker) -> None:
        tracker.record(1, tokens=10)
        tracker.record(2, tokens=20)
        # Sampler iterates the snapshot to compute the GC set; order
        # does not matter, presence does.
        assert set(tracker.known_pids()) == {1, 2}

    def test_known_pids_excludes_forgotten(self, tracker: TokenRateTracker) -> None:
        tracker.record(1, tokens=10)
        tracker.record(2, tokens=20)
        tracker.forget(1)
        assert set(tracker.known_pids()) == {2}


class TestThreadSafety:
    def test_concurrent_record_and_read_does_not_corrupt(self, tracker: TokenRateTracker) -> None:
        # The sampler writes from an asyncio executor while the view
        # reads from a worker thread. The lock must guarantee no
        # ``RuntimeError: deque mutated during iteration`` (which is
        # exactly the failure mode an unlocked deque + sum() race
        # would produce). 100 iterations is enough to make an
        # unlocked version fail reliably on macOS/Linux.
        stop = threading.Event()
        errors: list[Exception] = []

        def writer() -> None:
            for i in range(1000):
                if stop.is_set():
                    return
                try:
                    tracker.record(42, tokens=i % 10, model="claude-sonnet-4-5")
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                    return

        def reader() -> None:
            for _ in range(1000):
                if stop.is_set():
                    return
                try:
                    tracker.tokens_per_min(42)
                    tracker.latest_model(42)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                    return

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        stop.set()
        for t in threads:
            assert not t.is_alive(), f"thread {t.name} hung"
        assert errors == []
