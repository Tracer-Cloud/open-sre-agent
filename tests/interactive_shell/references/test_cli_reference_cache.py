"""Tests for interactive-shell CLI reference grounding cache."""

from __future__ import annotations

import pytest

from interactive_shell.agent_shell.grounding import cli_reference as cli_reference_module
from interactive_shell.agent_shell.grounding.cli_reference import CliReference


def test_second_build_is_cache_hit() -> None:
    ref = CliReference()
    ref.build_text()
    s1 = ref.stats()
    ref.build_text()
    s2 = ref.stats()
    assert s2.hits == s1.hits + 1
    assert s2.misses == s1.misses


def test_cold_build_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    from cli.__main__ import cli

    text = CliReference().build_text()
    captured = capsys.readouterr()
    first_command = sorted(cli.commands.keys())[0]

    assert captured.out == ""
    assert captured.err == ""
    assert "=== opensre --help ===" in text
    assert f"=== opensre {first_command} --help ===" in text
    assert f"Usage: opensre {first_command}" in text


def test_invalidate_forces_rebuild_miss() -> None:
    ref = CliReference()
    ref.build_text()
    s1 = ref.stats()
    assert s1.misses == 1
    ref.invalidate()
    assert ref.stats().misses == 0
    ref.build_text()
    s2 = ref.stats()
    assert s2.misses == 1
    assert s2.cached is True


def test_signature_change_busts_cli_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    ref = CliReference()
    monkeypatch.setattr(cli_reference_module, "_current_cli_signature", lambda: "sig-a")
    ref.build_text()
    monkeypatch.setattr(cli_reference_module, "_current_cli_signature", lambda: "sig-b")
    ref.build_text()
    stats = ref.stats()
    assert stats.misses >= 2
    assert stats.signature == "sig-b"


def test_invalidate_resets_hit_miss_counters() -> None:
    ref = CliReference()
    ref.build_text()
    ref.build_text()
    assert ref.stats().hits >= 1
    ref.invalidate()
    s = ref.stats()
    assert s.hits == 0
    assert s.misses == 0


def test_non_cacheable_short_output_skips_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_reference_module,
        "_build_cli_reference_text_uncached",
        lambda: "too short",
    )
    ref = CliReference()
    ref.build_text()
    ref.build_text()
    stats = ref.stats()
    assert stats.cached is False
    assert stats.misses >= 2


def test_non_cacheable_long_without_sentinel_skips_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filler = "x" * 120
    monkeypatch.setattr(
        cli_reference_module,
        "_build_cli_reference_text_uncached",
        lambda: filler,
    )
    ref = CliReference()
    ref.build_text()
    assert ref.stats().cached is False
