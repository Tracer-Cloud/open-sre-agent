"""Tests for REPL-safe RCA report rendering."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class _NoopTracker:
    def stop(self) -> None:
        return None


@patch.dict(os.environ, {"TRACER_OUTPUT_FORMAT": "rich"})
def test_render_report_uses_repl_buffered_write_in_repl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    class _FakeStdout:
        def write(self, text: str) -> int:
            writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr(
        "app.cli.interactive_shell.ui.output.environment._repl_progress_active",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.observability.progress.get_progress_tracker",
        lambda: _NoopTracker(),
    )
    monkeypatch.setattr(
        "app.observability.render_completed_investigation_footer",
        lambda: None,
    )

    from app.core.orchestration.node.publish_findings.renderers.terminal import render_report

    render_report("## Findings\n- Database connection refused\n")

    assert len(writes) == 1
    rendered = writes[0].replace("\r\n", "\n")
    assert "Database connection refused" in rendered
