"""Tests for interactive-shell CLI reference grounding cache."""

from __future__ import annotations

import pytest

from app.cli.interactive_shell import cli_reference as cli_reference_module
from app.cli.interactive_shell.cli_reference import (
    build_cli_reference_text,
    get_cli_reference_cache_stats,
    invalidate_cli_reference_cache,
)


def test_second_build_is_cache_hit() -> None:
    invalidate_cli_reference_cache()
    build_cli_reference_text()
    s1 = get_cli_reference_cache_stats()
    build_cli_reference_text()
    s2 = get_cli_reference_cache_stats()
    assert s2["hits"] == s1["hits"] + 1
    assert s2["misses"] == s1["misses"]


def test_invalidate_forces_rebuild_miss() -> None:
    invalidate_cli_reference_cache()
    build_cli_reference_text()
    s1 = get_cli_reference_cache_stats()
    invalidate_cli_reference_cache()
    build_cli_reference_text()
    s2 = get_cli_reference_cache_stats()
    assert s2["misses"] == s1["misses"] + 1


def test_signature_change_busts_cli_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    invalidate_cli_reference_cache()
    monkeypatch.setattr(cli_reference_module, "_current_cli_signature", lambda: "sig-a")
    build_cli_reference_text()
    monkeypatch.setattr(cli_reference_module, "_current_cli_signature", lambda: "sig-b")
    build_cli_reference_text()
    stats = get_cli_reference_cache_stats()
    assert stats["misses"] >= 2
    assert stats["signature"] == "sig-b"
