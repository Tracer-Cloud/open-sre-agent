from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.pipeline.pipeline import _retrieve_runbook


def _patch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "opensre_home"
    monkeypatch.setattr("app.constants.OPENSRE_HOME_DIR", home)
    return home


def _write_runbook(home: Path, slug: str, frontmatter: str, body: str = "body") -> None:
    target = home / "runbooks" / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")


def test_retrieve_runbook_matches_by_service_and_keywords(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    _write_runbook(
        home,
        "payments-oom",
        frontmatter="service: payments-api\ntriggers:\n  - oom\n  - memory",
    )

    state: dict[str, Any] = {
        "problem_md": "payments-api OOMKilled with memory exhaustion.",
        "alert_name": "PaymentsOOM",
        "pipeline_name": "payments-api",
        "alert_json": {"commonLabels": {"service": "payments-api"}},
    }

    matched = _retrieve_runbook(state)

    assert matched is not None
    assert matched["slug"] == "payments-oom"
    assert matched["service"] == "payments-api"


def test_retrieve_runbook_returns_none_when_no_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_home(monkeypatch, tmp_path)

    matched = _retrieve_runbook(
        {
            "problem_md": "",
            "alert_name": "SomethingElse",
            "pipeline_name": "other",
        }
    )

    assert matched is None


def test_retrieve_runbook_returns_none_on_empty_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_home(monkeypatch, tmp_path)

    matched = _retrieve_runbook({"alert_name": "Any", "pipeline_name": "any"})

    assert matched is None
