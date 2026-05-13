"""Tests for the pluggable grounding diagnostics registry."""

from __future__ import annotations

from app.cli.interactive_shell.references.grounding_diagnostics import (
    GroundingSource,
    iter_grounding_sources,
    log_grounding_cache_diagnostics,
    register_grounding_source,
)


def _make_source(name: str, hits: int = 0) -> GroundingSource:
    return GroundingSource(
        name=name,
        stats_fn=lambda: {"hits": hits},
        format_fn=lambda s: f"hits={s['hits']}",
    )


def test_register_and_iterate(tmp_path: object) -> None:
    """Registered sources appear in iter_grounding_sources."""
    from app.cli.interactive_shell.references import grounding_diagnostics as _gd

    original = dict(_gd._registry)
    _gd._registry.clear()
    try:
        src = _make_source("test_cli")
        register_grounding_source(src)
        sources = list(iter_grounding_sources())
        assert any(s.name == "test_cli" for s in sources)
    finally:
        _gd._registry.clear()
        _gd._registry.update(original)


def test_idempotent_registration() -> None:
    """Registering the same name twice updates in place, no duplicates."""
    from app.cli.interactive_shell.references import grounding_diagnostics as _gd

    original = dict(_gd._registry)
    _gd._registry.clear()
    try:
        register_grounding_source(_make_source("dup", hits=1))
        register_grounding_source(_make_source("dup", hits=2))
        sources = [s for s in iter_grounding_sources() if s.name == "dup"]
        assert len(sources) == 1
        assert sources[0].stats_fn()["hits"] == 2
    finally:
        _gd._registry.clear()
        _gd._registry.update(original)


def test_iteration_order() -> None:
    """Sources are returned in insertion order."""
    from app.cli.interactive_shell.references import grounding_diagnostics as _gd

    original = dict(_gd._registry)
    _gd._registry.clear()
    try:
        register_grounding_source(_make_source("first"))
        register_grounding_source(_make_source("second"))
        names = [s.name for s in iter_grounding_sources()]
        assert names == ["first", "second"]
    finally:
        _gd._registry.clear()
        _gd._registry.update(original)


def test_log_grounding_uses_registry(monkeypatch: object) -> None:
    """log_grounding_cache_diagnostics iterates the registry when verbose."""
    import os

    from app.cli.interactive_shell.references import grounding_diagnostics as _gd

    original = dict(_gd._registry)
    _gd._registry.clear()
    logged: list[str] = []

    try:
        monkeypatch.setenv("TRACER_VERBOSE", "1")
        register_grounding_source(_make_source("mock", hits=5))
        monkeypatch.setattr(
            _gd._logger,
            "debug",
            lambda msg, *args: logged.append(msg % args),
        )
        log_grounding_cache_diagnostics("test_reason")
        assert any("mock" in entry for entry in logged)
    finally:
        _gd._registry.clear()
        _gd._registry.update(original)
        os.environ.pop("TRACER_VERBOSE", None)
