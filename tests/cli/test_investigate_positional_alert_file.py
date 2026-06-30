"""Tests for the optional positional ``ALERT_FILE`` argument on
``opensre investigate``.

Before this change, ``opensre investigate alert.json`` failed with
``Error: Got unexpected extra argument (alert.json)``. The positional
shortcut is treated as equivalent to ``-i alert.json``; an explicit
``-i`` flag wins when both are passed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from surfaces.cli.commands.general import investigate_command


def _write_minimal_alert(tmp_path: Path) -> Path:
    """Write a syntactically valid alert payload tests can pass through the CLI."""
    alert = tmp_path / "alert.json"
    alert.write_text(
        '{"alert_name": "test", "pipeline_name": "etl", "severity": "warning"}',
        encoding="utf-8",
    )
    return alert


def test_positional_alert_file_is_accepted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``opensre investigate <path>`` must not error with 'unexpected extra argument'."""
    captured: dict[str, str | None] = {}

    def _capture_load_payload(*, input_path: str | None, **_kwargs: object) -> dict[str, str]:
        captured["input_path"] = input_path
        return {"alert_name": "test"}

    monkeypatch.setattr(
        "surfaces.cli.investigation.payload.load_payload",
        _capture_load_payload,
    )
    monkeypatch.setattr(
        "surfaces.cli.investigation.run_investigation_cli",
        lambda **_kwargs: {"report": "ok", "root_cause": "ok"},
    )
    monkeypatch.setattr(
        "surfaces.cli.investigation.run_investigation_cli_streaming",
        lambda **_kwargs: {"report": "ok", "root_cause": "ok"},
    )
    monkeypatch.setattr("surfaces.cli.write_json", lambda *_args, **_kw: None)

    alert = _write_minimal_alert(tmp_path)
    result = CliRunner().invoke(investigate_command, [str(alert)])

    assert "unexpected extra argument" not in result.output, result.output
    assert captured["input_path"] == str(alert)


def test_explicit_input_flag_wins_when_both_provided(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When both positional and ``-i`` are passed, ``-i`` takes precedence."""
    captured: dict[str, str | None] = {}

    def _capture_load_payload(*, input_path: str | None, **_kwargs: object) -> dict[str, str]:
        captured["input_path"] = input_path
        return {"alert_name": "test"}

    monkeypatch.setattr(
        "surfaces.cli.investigation.payload.load_payload",
        _capture_load_payload,
    )
    monkeypatch.setattr(
        "surfaces.cli.investigation.run_investigation_cli",
        lambda **_kwargs: {"report": "ok", "root_cause": "ok"},
    )
    monkeypatch.setattr(
        "surfaces.cli.investigation.run_investigation_cli_streaming",
        lambda **_kwargs: {"report": "ok", "root_cause": "ok"},
    )
    monkeypatch.setattr("surfaces.cli.write_json", lambda *_args, **_kw: None)

    positional = _write_minimal_alert(tmp_path)
    explicit = tmp_path / "explicit.json"
    explicit.write_text(
        '{"alert_name": "explicit", "pipeline_name": "etl", "severity": "warning"}',
        encoding="utf-8",
    )

    CliRunner().invoke(
        investigate_command,
        [str(positional), "-i", str(explicit)],
    )

    assert captured["input_path"] == str(explicit)
