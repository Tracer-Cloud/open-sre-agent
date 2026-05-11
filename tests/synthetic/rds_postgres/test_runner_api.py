from __future__ import annotations

import pytest

from tests.synthetic.rds_postgres.runner_api import (
    ShardSpec,
    SuiteRunConfig,
    group_fixtures_by_level,
    parse_levels_csv,
    parse_shard,
    select_fixtures,
)
from tests.synthetic.rds_postgres.scenario_loader import load_all_scenarios


def test_parse_levels_csv_defaults_and_deduplicates() -> None:
    assert parse_levels_csv("") == (1, 2, 3, 4)
    assert parse_levels_csv("2,1,2,4") == (2, 1, 4)


def test_parse_levels_csv_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        parse_levels_csv("0,1")


def test_parse_shard_defaults_and_parses() -> None:
    assert parse_shard("") == ShardSpec(index=0, total=1)
    assert parse_shard("1/4") == ShardSpec(index=1, total=4)


def test_select_fixtures_filters_by_level() -> None:
    fixtures = load_all_scenarios()
    config = SuiteRunConfig(levels=(3,), parallel_levels=1)

    selected = select_fixtures(fixtures, config)
    assert selected
    assert all(fixture.metadata.scenario_difficulty == 3 for fixture in selected)


def test_select_fixtures_scenario_bypasses_level_filter() -> None:
    fixtures = load_all_scenarios()
    scenario_id = "001-replication-lag"
    config = SuiteRunConfig(scenario=scenario_id, levels=(4,), parallel_levels=1)

    selected = select_fixtures(fixtures, config)
    assert [fixture.scenario_id for fixture in selected] == [scenario_id]


def test_group_fixtures_by_level_is_ordered() -> None:
    fixtures = load_all_scenarios()
    config = SuiteRunConfig(levels=(2, 1), parallel_levels=1)
    selected = select_fixtures(fixtures, config)

    grouped = group_fixtures_by_level(selected, config.levels)
    assert [group.level for group in grouped] == [2, 1]
