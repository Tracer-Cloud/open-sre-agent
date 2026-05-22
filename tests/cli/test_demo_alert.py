"""Bundled demo alert.json shipped for quick-start investigate flows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli.investigation.payload import (
    bundled_demo_alert_path,
    load_file,
    resolve_alert_path,
)


def test_bundled_demo_alert_fixture_exists() -> None:
    path = bundled_demo_alert_path()
    assert path is not None
    assert path.is_file()
    assert path.parent.name == "fixtures"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["pipeline_name"] == "payments_etl"
    assert path.name == "alert.json"


def test_resolve_alert_path_uses_bundled_demo_when_cwd_has_no_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "alert.json").exists()

    resolved = resolve_alert_path("alert.json")
    assert resolved.is_file()
    assert resolved == bundled_demo_alert_path()


def test_load_file_reads_bundled_demo_from_empty_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    payload = load_file("alert.json")
    assert payload["alert_source"] == "generic"
    assert payload["pipeline_name"] == "payments_etl"
