from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from tests.synthetic.rds_postgres.scenario_loader import SUITE_DIR, ScenarioFixture

DEFAULT_LEVELS: tuple[int, ...] = (1, 2, 3, 4)
_MAX_LEVEL = 4


@dataclass(frozen=True)
class ShardSpec:
    index: int = 0
    total: int = 1

    def __post_init__(self) -> None:
        if self.total < 1:
            raise ValueError("total shards must be >= 1")
        if self.index < 0 or self.index >= self.total:
            raise ValueError("shard index must satisfy 0 <= index < total")


@dataclass(frozen=True)
class SuiteRunConfig:
    scenario: str = ""
    levels: tuple[int, ...] = DEFAULT_LEVELS
    parallel_levels: int = 1
    output_json: bool = False
    mock_grafana: bool = False
    report: bool | None = None
    observations_dir: Path = field(default_factory=lambda: SUITE_DIR / "_observations")
    baseline_out: Path | None = None
    baseline_check: Path | None = None
    shard: ShardSpec = field(default_factory=ShardSpec)

    def __post_init__(self) -> None:
        if self.parallel_levels < 1:
            raise ValueError("parallel_levels must be >= 1")
        if not self.levels:
            raise ValueError("levels must not be empty")
        for level in self.levels:
            if level < 1 or level > _MAX_LEVEL:
                raise ValueError(f"level {level} is outside supported range 1..{_MAX_LEVEL}")


@dataclass(frozen=True)
class LevelRunConfig:
    level: int
    fixtures: tuple[ScenarioFixture, ...]


@dataclass(frozen=True)
class LevelRunResult:
    level: int
    scenario_ids: tuple[str, ...]
    passed: int
    failed: int
    wall_time_s: float


@dataclass(frozen=True)
class SuiteRunResult:
    config: SuiteRunConfig
    level_results: tuple[LevelRunResult, ...]
    scores: tuple[Any, ...]
    canonical_payloads: dict[str, Any]


def parse_levels_csv(raw: str | None) -> tuple[int, ...]:
    text = (raw or "").strip()
    if not text:
        return DEFAULT_LEVELS

    seen: set[int] = set()
    ordered: list[int] = []
    for token in text.split(","):
        value = token.strip()
        if not value:
            continue
        level = int(value)
        if level < 1 or level > _MAX_LEVEL:
            raise ValueError(f"level {level} is outside supported range 1..{_MAX_LEVEL}")
        if level not in seen:
            seen.add(level)
            ordered.append(level)

    if not ordered:
        raise ValueError("levels must contain at least one integer")
    return tuple(ordered)


def parse_shard(raw: str | None) -> ShardSpec:
    text = (raw or "").strip()
    if not text:
        return ShardSpec()

    if "/" not in text:
        raise ValueError("shard must be formatted as INDEX/TOTAL, e.g. 0/4")

    left, right = text.split("/", 1)
    index = int(left.strip())
    total = int(right.strip())
    return ShardSpec(index=index, total=total)


def select_fixtures(
    fixtures: list[ScenarioFixture], config: SuiteRunConfig
) -> list[ScenarioFixture]:
    selected = fixtures

    if config.scenario:
        selected = [fixture for fixture in selected if fixture.scenario_id == config.scenario]
        if not selected:
            raise ValueError(f"Unknown scenario: {config.scenario}")
        return selected

    selected_levels = set(config.levels)
    selected = [
        fixture for fixture in selected if fixture.metadata.scenario_difficulty in selected_levels
    ]

    if config.shard.total > 1:

        def _stable_mod(text: str, mod: int) -> int:
            digest = sha256(text.encode("utf-8")).digest()
            return int.from_bytes(digest[:8], "big") % mod

        selected = [
            fixture
            for fixture in selected
            if _stable_mod(fixture.scenario_id, config.shard.total) == config.shard.index
        ]

    return selected


def group_fixtures_by_level(
    fixtures: list[ScenarioFixture],
    levels: tuple[int, ...],
) -> tuple[LevelRunConfig, ...]:
    grouped: list[LevelRunConfig] = []
    for level in levels:
        level_fixtures = tuple(
            sorted(
                (fixture for fixture in fixtures if fixture.metadata.scenario_difficulty == level),
                key=lambda fixture: fixture.scenario_id,
            )
        )
        if level_fixtures:
            grouped.append(LevelRunConfig(level=level, fixtures=level_fixtures))
    return tuple(grouped)
