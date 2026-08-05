"""Tests for cross-process scheduler reload signaling."""

from __future__ import annotations

from pathlib import Path

import pytest

from platform.scheduler import reload_signal


@pytest.fixture()
def _tmp_signal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "scheduler_reload_requested"
    monkeypatch.setattr(reload_signal, "_signal_path", lambda: path)


@pytest.mark.usefixtures("_tmp_signal")
def test_request_and_consume_scheduler_reload() -> None:
    assert reload_signal.consume_scheduler_reload_request() is False
    reload_signal.request_scheduler_reload()
    assert reload_signal.consume_scheduler_reload_request() is True
    assert reload_signal.consume_scheduler_reload_request() is False
