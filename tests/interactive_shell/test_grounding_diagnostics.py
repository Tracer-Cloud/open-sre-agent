"""Tests for the session-scoped grounding context and its diagnostics sources."""

from __future__ import annotations

from core.agent_harness.grounding.context import GroundingContext
from core.agent_harness.grounding.diagnostics import (
    GroundingSource,
    log_grounding_cache_diagnostics,
)
from core.agent_harness.grounding.models import CacheStats
from core.agent_harness.session.state import Session
from surfaces.interactive_shell.grounding.cli_reference import ShellPromptContextProvider


def _make_source(name: str, hits: int = 0) -> GroundingSource:
    return GroundingSource(name=name, stats_fn=lambda: CacheStats(name=name, hits=hits))


def test_context_exposes_one_source_per_cache() -> None:
    """A GroundingContext yields a diagnostics source for each repo-level cache."""
    ctx = GroundingContext()
    names = [s.name for s in ctx.iter_sources()]
    assert names == ["docs", "agents_md"]


def test_context_sources_are_isolated_per_instance() -> None:
    """Two contexts own independent caches (no shared module-level state)."""
    ctx_a = GroundingContext()
    ctx_b = GroundingContext()
    assert ctx_a.docs is not ctx_b.docs
    assert ctx_a.agents_md is not ctx_b.agents_md


def test_invalidate_clears_every_cache() -> None:
    ctx = GroundingContext()
    ctx.agents_md.build_text()
    assert ctx.agents_md.stats().misses >= 1
    ctx.invalidate()
    assert ctx.agents_md.stats().misses == 0


def test_shell_prompt_provider_cli_cache_is_session_scoped() -> None:
    session_a = Session()
    session_b = Session()
    provider_a = ShellPromptContextProvider(session_a)
    provider_b = ShellPromptContextProvider(session_a)
    provider_c = ShellPromptContextProvider(session_b)
    provider_a.cli_reference()
    provider_b.cli_reference()
    assert provider_b._cli.stats().hits >= 1  # noqa: SLF001 - same session shares cache
    provider_c.cli_reference()
    assert provider_c._cli.stats().misses >= 1  # noqa: SLF001 - different session is isolated


def test_log_grounding_iterates_provided_sources(monkeypatch: object) -> None:
    """log_grounding_cache_diagnostics logs each provided source when verbose."""
    import os

    from core.agent_harness.grounding import diagnostics as _gd

    logged: list[str] = []
    try:
        monkeypatch.setenv("TRACER_VERBOSE", "1")  # type: ignore[attr-defined]
        monkeypatch.setattr(  # type: ignore[attr-defined]
            _gd._logger,
            "debug",
            lambda msg, *args: logged.append(msg % args),
        )
        log_grounding_cache_diagnostics([_make_source("mock", hits=5)], "test_reason")
        assert any("mock" in entry for entry in logged)
    finally:
        os.environ.pop("TRACER_VERBOSE", None)
