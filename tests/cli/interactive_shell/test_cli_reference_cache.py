"""Tests for interactive-shell CLI reference grounding cache."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.cli.interactive_shell import cli_reference as cli_reference_module
from app.cli.interactive_shell.cli_reference import (
    build_cli_reference_text,
    get_cli_reference_cache_stats,
    invalidate_cli_reference_cache,
)


@pytest.fixture(autouse=True)
def _reset_cli_reference_cache() -> Iterator[None]:
    invalidate_cli_reference_cache()
    yield
    invalidate_cli_reference_cache()


def test_second_build_is_cache_hit() -> None:
    build_cli_reference_text()
    s1 = get_cli_reference_cache_stats()
    build_cli_reference_text()
    s2 = get_cli_reference_cache_stats()
    assert s2["hits"] == s1["hits"] + 1
    assert s2["misses"] == s1["misses"]


def test_cold_build_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    from app.cli.__main__ import cli

    text = build_cli_reference_text()
    captured = capsys.readouterr()
    first_command = sorted(cli.commands.keys())[0]

    assert captured.out == ""
    assert captured.err == ""
    assert "=== opensre --help ===" in text
    assert f"=== opensre {first_command} --help ===" in text
    assert f"Usage: opensre {first_command}" in text


def test_invalidate_forces_rebuild_miss() -> None:
    build_cli_reference_text()
    s1 = get_cli_reference_cache_stats()
    assert s1["misses"] == 1
    invalidate_cli_reference_cache()
    assert get_cli_reference_cache_stats()["misses"] == 0
    build_cli_reference_text()
    s2 = get_cli_reference_cache_stats()
    assert s2["misses"] == 1
    assert s2["cached"] is True


def test_signature_change_busts_cli_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_reference_module, "_current_cli_signature", lambda: "sig-a")
    build_cli_reference_text()
    monkeypatch.setattr(cli_reference_module, "_current_cli_signature", lambda: "sig-b")
    build_cli_reference_text()
    stats = get_cli_reference_cache_stats()
    assert stats["misses"] >= 2
    assert stats["signature"] == "sig-b"


def test_invalidate_resets_hit_miss_counters() -> None:
    build_cli_reference_text()
    build_cli_reference_text()
    assert get_cli_reference_cache_stats()["hits"] >= 1
    invalidate_cli_reference_cache()
    s = get_cli_reference_cache_stats()
    assert s["hits"] == 0
    assert s["misses"] == 0


def test_non_cacheable_short_output_skips_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_reference_module,
        "_build_cli_reference_text_uncached",
        lambda: "too short",
    )
    build_cli_reference_text()
    build_cli_reference_text()
    stats = get_cli_reference_cache_stats()
    assert stats["cached"] is False
    assert stats["misses"] >= 2


def test_non_cacheable_long_without_sentinel_skips_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filler = "x" * 120
    monkeypatch.setattr(
        cli_reference_module,
        "_build_cli_reference_text_uncached",
        lambda: filler,
    )
    build_cli_reference_text()
    assert get_cli_reference_cache_stats()["cached"] is False
