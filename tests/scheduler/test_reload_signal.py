"""The cross-process scheduler reload signal is atomic and best-effort."""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler import reload_signal


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the signal file into a temp home."""
    monkeypatch.setattr(reload_signal, "OPENSRE_HOME_DIR", tmp_path)
    return tmp_path


def test_request_then_consume_is_a_single_shot(home: Path) -> None:
    # Nothing pending; requesting makes one consume True; then it clears.
    assert reload_signal.consume_scheduler_reload_request() is False
    reload_signal.request_scheduler_reload()
    assert reload_signal.consume_scheduler_reload_request() is True
    assert reload_signal.consume_scheduler_reload_request() is False


def test_request_is_best_effort_on_write_failure(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unwritable home must not raise — a failed signal cannot fail the store
    # mutation that triggered it (the scheduler resyncs on its next poll/restart).
    def _unwritable(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", _unwritable)

    reload_signal.request_scheduler_reload()  # must not raise

    assert reload_signal.consume_scheduler_reload_request() is False
