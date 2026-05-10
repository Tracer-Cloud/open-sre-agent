"""Tests for optional grounding-cache diagnostic logging."""

from __future__ import annotations

import logging

import pytest

from app.cli.interactive_shell import (
    agents_md_reference,
    cli_reference,
    docs_reference,
)
from app.cli.interactive_shell.grounding_diagnostics import (
    GroundingSource,
    iter_grounding_sources,
    log_grounding_cache_diagnostics,
    register_grounding_source,
    unregister_grounding_source,
)


@pytest.fixture(autouse=True)
def restore_grounding_source_registry() -> None:
    snapshot_names = {source.name for source in iter_grounding_sources()}
    try:
        yield
    finally:
        for source in list(iter_grounding_sources()):
            if source.name not in snapshot_names:
                unregister_grounding_source(source.name)
        cli_reference._register_grounding_source()
        docs_reference._register_grounding_source()
        agents_md_reference._register_grounding_source()


def test_log_skips_when_tracer_verbose_unset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("TRACER_VERBOSE", raising=False)
    with caplog.at_level(logging.DEBUG):
        log_grounding_cache_diagnostics("unit_test")
    assert not caplog.records


def test_log_skips_when_tracer_verbose_not_one(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TRACER_VERBOSE", "0")
    with caplog.at_level(logging.DEBUG):
        log_grounding_cache_diagnostics("unit_test")
    assert not caplog.records


def test_iter_grounding_sources_includes_builtin_sources_in_order() -> None:
    names = [source.name for source in iter_grounding_sources()]
    assert "cli" in names
    assert "docs" in names
    assert "agents_md" in names
    assert names.index("cli") < names.index("docs")


def test_register_grounding_source_reregisters_same_name_without_duplication() -> None:
    register_grounding_source(
        GroundingSource(
            name="custom_idempotent_source",
            stats_fn=lambda: {"hits": 1},
            format_fn=lambda _stats: "first",
        )
    )
    register_grounding_source(
        GroundingSource(
            name="custom_idempotent_source",
            stats_fn=lambda: {"hits": 2},
            format_fn=lambda _stats: "second",
        )
    )

    custom_sources = [
        source for source in iter_grounding_sources() if source.name == "custom_idempotent_source"
    ]
    assert len(custom_sources) == 1
    assert custom_sources[0].format_fn(custom_sources[0].stats_fn()) == "second"


def test_log_emits_registered_source_stats_when_tracer_verbose_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TRACER_VERBOSE", "1")
    register_grounding_source(
        GroundingSource(
            name="custom_log_source",
            stats_fn=lambda: {"hits": 3, "misses": 1},
            format_fn=lambda stats: f"hits={stats['hits']} misses={stats['misses']}",
        )
    )
    with caplog.at_level(logging.DEBUG):
        log_grounding_cache_diagnostics("unit_test_reason")
    assert any("unit_test_reason" in r.message for r in caplog.records)
    assert any("grounding cache" in r.message.lower() for r in caplog.records)
    assert any("cli={" in r.message for r in caplog.records)
    assert any("docs={" in r.message for r in caplog.records)
    assert any("agents_md={" in r.message for r in caplog.records)
    assert any("custom_log_source={'hits': 3, 'misses': 1}" in r.message for r in caplog.records)
