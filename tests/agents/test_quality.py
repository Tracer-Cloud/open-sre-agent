"""Tests for agent stream quality helpers (loop detection)."""

from __future__ import annotations

import pytest

from app.agents.quality import LoopDetector, _shingle_fingerprint


class TestShingleFingerprint:
    def test_stable_for_identical_shingle(self) -> None:
        s = ("hello", "world", "again")
        assert _shingle_fingerprint(s) == _shingle_fingerprint(s)

    def test_distinct_for_different_shingles(self) -> None:
        a = _shingle_fingerprint(("a", "bc"))
        b = _shingle_fingerprint(("ab", "c"))
        assert a != b


class TestLoopDetector:
    def test_stores_window_configuration(self) -> None:
        det = LoopDetector(window=17, threshold=1, shingle_size=2)
        assert det._window == 17
        assert det._shingle_hashes.maxlen == 17

    def test_defaults_non_looping_on_varied_stream(self) -> None:
        det = LoopDetector(window=20, threshold=4, shingle_size=3)
        phrases = [
            "read the config file",
            "then check docker logs",
            "finally curl the health endpoint",
        ]
        for _ in range(15):
            for p in phrases:
                det.observe(p + "\n")
        assert det.is_looping is False

    def test_repeated_shingle_triggers_loop(self) -> None:
        det = LoopDetector(window=20, threshold=4, shingle_size=3)
        # Identical length-3 shingles: mimics "I'll help you" style repeats in output.
        for _ in range(30):
            det.observe("a a a ")
        assert det.is_looping is True

    def test_alternating_two_shingles_triggers_loop(self) -> None:
        det = LoopDetector(window=12, threshold=3, shingle_size=2)
        for _ in range(40):
            det.observe("alpha ")
            det.observe("beta ")
        assert det.is_looping is True

    def test_clear_resets_state(self) -> None:
        det = LoopDetector(window=20, threshold=4, shingle_size=3)
        for _ in range(80):
            det.observe("spam eggs ham ")
        assert det.is_looping is True
        det.clear()
        assert det.is_looping is False
        det.observe("fresh tokens here now")
        assert det.is_looping is False

    def test_at_threshold_not_looping(self) -> None:
        det = LoopDetector(window=50, threshold=4, shingle_size=2)
        # Two "a a " chunks emit three (a, a) shingles; one more lone "a " emits a fourth — still == threshold.
        det.observe("a a ")
        det.observe("a a ")
        det.observe("a ")
        assert det.is_looping is False

    def test_one_past_threshold_loops(self) -> None:
        det = LoopDetector(window=50, threshold=4, shingle_size=2)
        det.observe("a a ")
        det.observe("a a ")
        det.observe("a ")
        det.observe("a ")
        assert det.is_looping is True

    def test_sliding_window_recovers_when_repetition_ages_out(self) -> None:
        det = LoopDetector(window=6, threshold=2, shingle_size=2)
        for _ in range(10):
            det.observe("x y ")
        assert det.is_looping is True
        for i in range(30):
            det.observe(f"noise{i} token ")
        assert det.is_looping is False

    @pytest.mark.parametrize(
        ("window", "threshold", "shingle_size"),
        [
            (0, 4, 3),
            (10, -1, 3),
            (10, 4, 0),
        ],
    )
    def test_invalid_constructor_raises(
        self, window: int, threshold: int, shingle_size: int
    ) -> None:
        with pytest.raises(ValueError):
            LoopDetector(window=window, threshold=threshold, shingle_size=shingle_size)
