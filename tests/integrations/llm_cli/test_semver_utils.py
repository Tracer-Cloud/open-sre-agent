from __future__ import annotations

from integrations.llm_cli.semver_utils import parse_semver_three_part, semver_to_tuple


def test_parse_semver_three_part_finds_version() -> None:
    assert parse_semver_three_part("tool v1.2.3-beta") == "1.2.3"


def test_parse_semver_three_part_returns_none_when_missing() -> None:
    assert parse_semver_three_part("no version here") is None


def test_semver_to_tuple_handles_missing_parts() -> None:
    assert semver_to_tuple("1") == (1, 0, 0)
    assert semver_to_tuple("1.2") == (1, 2, 0)


def test_semver_to_tuple_handles_suffixes() -> None:
    assert semver_to_tuple("1.2.3-beta.4") == (1, 2, 3)
