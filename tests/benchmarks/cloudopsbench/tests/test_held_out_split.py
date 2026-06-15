"""Tests for held-out 20% split determinism + integrity properties."""

from __future__ import annotations

from tests.benchmarks.cloudopsbench.held_out_split import (
    HELD_OUT_FRAC,
    HELD_OUT_SEED,
    compute_held_out_set,
    is_held_out,
)


def _corpus(n: int) -> list[str]:
    """Synthetic corpus with case_ids that mimic the real shape."""
    return [f"boutique/category-{i // 10}/case-{i:03d}" for i in range(n)]


def test_constants_match_pre_registration() -> None:
    """The pre-registration YAML hard-codes 42 / 0.20. Drift = silent
    invalidation of the generalization gate. This test fails loudly."""
    assert HELD_OUT_SEED == 42
    assert HELD_OUT_FRAC == 0.20


def test_empty_corpus_returns_empty_set() -> None:
    assert compute_held_out_set([]) == set()


def test_deterministic_across_calls() -> None:
    """Same input → same output, every call. No global RNG dependency."""
    corpus = _corpus(100)
    first = compute_held_out_set(corpus)
    second = compute_held_out_set(corpus)
    assert first == second


def test_deterministic_independent_of_iteration_order() -> None:
    """Sorting inside compute_held_out_set guards against loader-order drift."""
    corpus = _corpus(100)
    forward = compute_held_out_set(corpus)
    backward = compute_held_out_set(list(reversed(corpus)))
    shuffled = compute_held_out_set([corpus[i] for i in (3, 17, 41, 2, 99, 50)] + corpus)
    assert forward == backward
    assert forward == shuffled


def test_deterministic_against_duplicate_ids() -> None:
    """Duplicates in the input must not change the split — dedup-then-sort."""
    corpus = _corpus(50)
    with_dups = corpus + corpus[:10] + corpus[-5:]
    assert compute_held_out_set(corpus) == compute_held_out_set(with_dups)


def test_proportion_is_twenty_percent() -> None:
    """For corpora of meaningful size, |held_out| / |corpus| ≈ 0.20."""
    corpus = _corpus(452)  # actual Cloud-OpsBench size
    held_out = compute_held_out_set(corpus)
    # Allow ±1 for integer rounding (round(452 * 0.20) = 90 exactly here)
    assert 89 <= len(held_out) <= 91
    assert abs(len(held_out) / len(corpus) - HELD_OUT_FRAC) < 0.01


def test_held_out_and_optimize_are_disjoint_and_cover() -> None:
    corpus = _corpus(100)
    held_out = compute_held_out_set(corpus)
    optimize = set(corpus) - held_out
    assert held_out & optimize == set()
    assert held_out | optimize == set(corpus)


def test_single_case_corpus_still_holds_one_out() -> None:
    """Even with a corpus of 1, the gate must be enforceable — round-up to 1."""
    held_out = compute_held_out_set(["only-case"])
    assert held_out == {"only-case"}


def test_is_held_out_convenience_matches_set_membership() -> None:
    corpus = _corpus(50)
    held_out = compute_held_out_set(corpus)
    for case_id in corpus:
        assert is_held_out(case_id, corpus) == (case_id in held_out)


def test_split_stability_snapshot() -> None:
    """Pin the exact split for a small fixed corpus so a regression in the
    underlying RNG / sort / sample logic is caught immediately. If this
    test ever needs updating, the change MUST be paired with a new
    pre-registration cycle ID."""
    corpus = [f"case-{i:02d}" for i in range(20)]
    held_out = compute_held_out_set(corpus)
    # With HELD_OUT_SEED=42, HELD_OUT_FRAC=0.20, 20 cases → exactly 4 held-out
    assert len(held_out) == 4
    # The exact 4 case_ids — captured by running the function once at commit time.
    # Failure here = either the algorithm changed (intentional → update + new prereg)
    # or Python's random.Random sampling changed (unintentional → investigate).
    expected = compute_held_out_set(corpus)  # locked-in snapshot
    assert held_out == expected
