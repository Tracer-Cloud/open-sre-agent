"""Tests for optional grounding-cache diagnostic logging."""

from __future__ import annotations

import logging

import pytest

from app.cli.interactive_shell.grounding_diagnostics import log_grounding_cache_diagnostics


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


def test_log_emits_debug_when_tracer_verbose_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TRACER_VERBOSE", "1")
    with caplog.at_level(logging.DEBUG):
        log_grounding_cache_diagnostics("unit_test_reason")
    assert any("unit_test_reason" in r.message for r in caplog.records)
    assert any("grounding cache" in r.message.lower() for r in caplog.records)
