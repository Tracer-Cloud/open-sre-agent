"""Duration-balanced file sharding used by the pull-request test matrix."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


@dataclass(frozen=True, slots=True)
class FileTiming:
    """Recorded aggregate duration and test count for one test file."""

    seconds: float
    tests: int


def assign_file_groups(
    file_test_counts: Mapping[str, int],
    timings: Mapping[str, FileTiming],
    splits: int,
) -> dict[str, int]:
    """Assign every test file to a one-based least-duration group."""
    if splits < 1:
        raise ValueError("splits must be at least one")

    recorded_tests = sum(timing.tests for timing in timings.values())
    recorded_seconds = sum(timing.seconds for timing in timings.values())
    default_test_seconds = recorded_seconds / recorded_tests if recorded_tests else 1.0

    estimates: dict[str, float] = {}
    for path, test_count in file_test_counts.items():
        timing = timings.get(path)
        if timing is None:
            estimates[path] = test_count * default_test_seconds
            continue
        new_tests = max(0, test_count - timing.tests)
        estimates[path] = timing.seconds + new_tests * default_test_seconds

    totals = [0.0] * splits
    assignments: dict[str, int] = {}
    for path in sorted(estimates, key=lambda item: (-estimates[item], item)):
        group_index = min(range(splits), key=lambda index: (totals[index], index))
        assignments[path] = group_index + 1
        totals[group_index] += estimates[path]
    return assignments


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register CI-only sharding options."""
    group = parser.getgroup("OpenSRE CI sharding")
    group.addoption("--ci-splits", type=int)
    group.addoption("--ci-group", type=int)
    group.addoption("--ci-durations-path", type=Path)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Select the duration-balanced file group requested by CI."""
    splits = config.getoption("--ci-splits")
    selected_group = config.getoption("--ci-group")
    durations_path = config.getoption("--ci-durations-path")
    if splits is None and selected_group is None and durations_path is None:
        return
    if not isinstance(splits, int) or splits < 1:
        raise pytest.UsageError("--ci-splits must be at least one")
    if not isinstance(selected_group, int) or not 1 <= selected_group <= splits:
        raise pytest.UsageError("--ci-group must be between one and --ci-splits")
    if not isinstance(durations_path, Path):
        raise pytest.UsageError("--ci-durations-path is required with CI sharding")

    file_items: dict[str, list[pytest.Item]] = defaultdict(list)
    root = Path(config.rootpath).resolve()
    for item in items:
        try:
            path = Path(item.path).resolve().relative_to(root).as_posix()
        except ValueError:
            path = item.nodeid.split("::", maxsplit=1)[0]
        file_items[path].append(item)

    timings = _load_timings(durations_path)
    assignments = assign_file_groups(
        {path: len(grouped) for path, grouped in file_items.items()},
        timings,
        splits,
    )
    selected = [
        item
        for path, grouped in file_items.items()
        if assignments[path] == selected_group
        for item in grouped
    ]
    selected_items = set(selected)
    deselected = [item for item in items if item not in selected_items]
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)


def _load_timings(path: Path) -> dict[str, FileTiming]:
    """Load the checked-in aggregate timing snapshot."""
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise pytest.UsageError(f"CI durations file must be an object: {path}")
    timings: dict[str, FileTiming] = {}
    for file_path, value in raw.items():
        if not isinstance(file_path, str) or not isinstance(value, dict):
            raise pytest.UsageError(f"Invalid CI duration entry in {path}")
        seconds = value.get("seconds")
        tests = value.get("tests")
        if not isinstance(seconds, int | float) or not isinstance(tests, int):
            raise pytest.UsageError(f"Invalid CI duration entry for {file_path}")
        timings[file_path] = FileTiming(seconds=float(seconds), tests=tests)
    return timings
