"""End-to-end tests for the ``opensre misses`` command group."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from core.domain.feedback import MissTaxonomy, record_miss
from surfaces.cli.__main__ import cli

# Seed misses a couple of days in the past: comfortably inside the ``--since 30d``
# export window regardless of the current date. A hardcoded date here drifts to
# the 30-day boundary over time and makes the export test calendar-flaky.
_SEED_TIMESTAMP = (datetime.now(UTC) - timedelta(days=2)).isoformat()


@pytest.fixture
def opensre_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / ".opensre"
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", home)
    return home


def _seed(
    alert: str,
    taxonomy: MissTaxonomy,
    *,
    feedback_id: str = "fb",
    timestamp: str | None = None,
) -> dict:
    return record_miss(
        {
            "feedback_id": feedback_id,
            "timestamp": timestamp or _SEED_TIMESTAMP,
            "run_id": f"run-{feedback_id}",
            "alert_name": alert,
            "rating": "inaccurate",
            "note": "n",
            "root_cause": "rc",
        },
        taxonomy=taxonomy,
    )


def test_list_empty_explains_store_path(opensre_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["misses", "list"])
    assert result.exit_code == 0
    assert "No misses recorded" in result.output


def test_list_renders_table_for_seeded_rows(opensre_home: Path) -> None:
    _seed("alert-A", MissTaxonomy.RETRIEVAL_GAP, feedback_id="fb-1")
    _seed("alert-B", MissTaxonomy.TOOL_FAILURE, feedback_id="fb-2")

    runner = CliRunner()
    result = runner.invoke(cli, ["misses", "list"])

    assert result.exit_code == 0
    assert "alert-A" in result.output
    assert "alert-B" in result.output
    assert "retrieval_gap" in result.output
    assert "tool_failure" in result.output


def test_list_json_emits_machine_readable(opensre_home: Path) -> None:
    _seed("alert-A", MissTaxonomy.RETRIEVAL_GAP)
    runner = CliRunner()
    result = runner.invoke(cli, ["misses", "list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert rows[0]["alert_name"] == "alert-A"
    assert rows[0]["taxonomy"] == "retrieval_gap"


def test_list_filters_by_taxonomy(opensre_home: Path) -> None:
    _seed("alert-A", MissTaxonomy.RETRIEVAL_GAP, feedback_id="fb-1")
    _seed("alert-B", MissTaxonomy.TOOL_FAILURE, feedback_id="fb-2")

    runner = CliRunner()
    result = runner.invoke(cli, ["misses", "list", "--taxonomy", "tool_failure", "--json"])
    rows = json.loads(result.output)
    assert [r["alert_name"] for r in rows] == ["alert-B"]


def test_stats_reports_recurring_pairs(opensre_home: Path) -> None:
    _seed("alert-A", MissTaxonomy.RETRIEVAL_GAP, feedback_id="fb-1")
    _seed("alert-A", MissTaxonomy.RETRIEVAL_GAP, feedback_id="fb-2")
    _seed("alert-B", MissTaxonomy.TOOL_FAILURE, feedback_id="fb-3")

    runner = CliRunner()
    result = runner.invoke(cli, ["misses", "stats", "--json"])

    assert result.exit_code == 0
    stats = json.loads(result.output)
    assert stats["total"] == 3
    assert stats["by_taxonomy"]["retrieval_gap"] == 2
    recurring = {(a, t, c) for a, t, c in stats["recurring"]}
    assert ("alert-A", "retrieval_gap", 2) in recurring


def test_export_writes_alert_json_files(opensre_home: Path, tmp_path: Path) -> None:
    _seed("alert-A", MissTaxonomy.RETRIEVAL_GAP, feedback_id="fb-1")
    _seed("alert-A", MissTaxonomy.RETRIEVAL_GAP, feedback_id="fb-2")
    _seed("alert-B", MissTaxonomy.TOOL_FAILURE, feedback_id="fb-3")

    out_dir = tmp_path / "scenarios"
    runner = CliRunner()
    result = runner.invoke(
        cli, ["misses", "export", "--since", "30d", "--top", "5", "--out", str(out_dir)]
    )

    assert result.exit_code == 0, result.output
    written = sorted(out_dir.rglob("alert.json"))
    assert len(written) == 2  # deduped to one per (alert, taxonomy)

    payload = json.loads(written[0].read_text())
    assert payload["alert_source"] == "closed_loop_learning"
    assert payload["_meta"]["taxonomy"] in {"retrieval_gap", "tool_failure"}


def test_export_warns_when_no_misses(opensre_home: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["misses", "export", "--since", "1h", "--out", str(tmp_path / "scenarios")],
    )
    assert result.exit_code == 0
    assert "No misses" in result.output


def test_convert_unknown_miss_id_errors(opensre_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["misses", "convert", "does-not-exist"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_convert_writes_scenario_to_file(opensre_home: Path, tmp_path: Path) -> None:
    rec = _seed("alert-A", MissTaxonomy.RETRIEVAL_GAP)
    target = tmp_path / "scenario.json"

    runner = CliRunner()
    result = runner.invoke(cli, ["misses", "convert", rec["miss_id"], "--out", str(target)])

    assert result.exit_code == 0
    payload = json.loads(target.read_text())
    assert payload["_meta"]["miss_id"] == rec["miss_id"]
