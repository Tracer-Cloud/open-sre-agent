"""Contracts for duration-balanced pull-request test sharding."""

from __future__ import annotations

from collections import defaultdict

from tests.ci_sharding import FileTiming, assign_file_groups


def test_slow_files_are_balanced_across_groups() -> None:
    counts = {f"tests/test_{index}.py": 10 for index in range(6)}
    timings = {
        path: FileTiming(seconds=seconds, tests=10)
        for path, seconds in zip(counts, (30.0, 29.0, 20.0, 19.0, 10.0, 9.0))
    }

    assignments = assign_file_groups(counts, timings, splits=3)

    totals: dict[int, float] = defaultdict(float)
    for path, group in assignments.items():
        totals[group] += timings[path].seconds
    assert set(assignments) == set(counts)
    assert set(assignments.values()) == {1, 2, 3}
    assert max(totals.values()) - min(totals.values()) <= 1.0


def test_new_files_use_the_recorded_per_test_average() -> None:
    counts = {"tests/known.py": 2, "tests/new.py": 4}
    timings = {"tests/known.py": FileTiming(seconds=2.0, tests=2)}

    assignments = assign_file_groups(counts, timings, splits=2)

    assert assignments == {"tests/new.py": 1, "tests/known.py": 2}
