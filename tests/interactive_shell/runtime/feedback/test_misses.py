"""Unit tests for the miss triage store and benchmark scenario conversion."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.domain.feedback import (
    MissRecord,
    MissTaxonomy,
    compute_recurrence,
    compute_stats,
    load_misses,
    record_miss,
    to_benchmark_scenario,
)
from core.domain.feedback.misses import (
    export_scenarios,
    filter_top_misses,
    misses_path,
    parse_since,
    taxonomy_choices,
)


@pytest.fixture
def opensre_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / ".opensre"
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", home)
    return home


# ── taxonomy enum surface ─────────────────────────────────────────────────────


def test_taxonomy_choices_includes_all_buckets() -> None:
    keys = {key for key, _ in taxonomy_choices()}
    assert keys == {t.value for t in MissTaxonomy}


# ── record_miss / load_misses round-trip ──────────────────────────────────────


def _feedback_dict(**overrides) -> dict:
    base = {
        "feedback_id": "fb-001",
        "timestamp": "2026-06-02T10:00:00+00:00",
        "run_id": "run-001",
        "alert_name": "checkout-api 5xx",
        "rating": "inaccurate",
        "note": "missed the canary deploy from 09:42",
        "root_cause": "The investigation concluded a DB outage.",
        "root_cause_category": "database",
        "validity_score": 0.45,
        "investigation_loop_count": 6,
        "user_id": "u-1",
        "user_email": "u@example.com",
        "org_id": "org-1",
    }
    base.update(overrides)
    return base


def test_record_miss_returns_none_and_warns_on_write_failure(
    opensre_home: Path, monkeypatch, capsys
) -> None:
    def _explode(*_args, **_kwargs):
        raise PermissionError("disk full")

    monkeypatch.setattr("pathlib.Path.open", _explode)

    result = record_miss(_feedback_dict(), taxonomy=MissTaxonomy.RETRIEVAL_GAP)

    assert result is None
    err = capsys.readouterr().err
    assert "could not record miss" in err
    assert "disk full" in err


def test_record_miss_persists_to_jsonl(opensre_home: Path) -> None:
    feedback = _feedback_dict()
    final_state = {"pipeline_name": "checkout/api", "severity": "critical"}

    rec = record_miss(
        feedback,
        taxonomy=MissTaxonomy.RETRIEVAL_GAP,
        final_state=final_state,
    )

    assert rec is not None
    assert rec["taxonomy"] == "retrieval_gap"
    assert rec["pipeline_name"] == "checkout/api"
    assert rec["severity"] == "critical"
    assert rec["feedback_id"] == "fb-001"

    path = misses_path()
    assert path.exists()
    written = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(written) == 1
    assert written[0]["miss_id"] == rec["miss_id"]


def test_record_miss_accepts_string_taxonomy(opensre_home: Path) -> None:
    rec = record_miss(_feedback_dict(), taxonomy="reasoning_gap")
    assert rec is not None
    assert rec["taxonomy"] == "reasoning_gap"


def test_load_misses_skips_corrupt_lines(opensre_home: Path) -> None:
    path = misses_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    valid = {
        "miss_id": "ok",
        "alert_name": "a",
        "taxonomy": "tool_failure",
        "timestamp": "2026-06-02T00:00:00+00:00",
    }
    path.write_text(
        "\n".join(
            [
                json.dumps(valid),
                "not json at all",
                "",
                '{"miss_id": "partial"',  # malformed
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = load_misses()
    assert [r["miss_id"] for r in rows] == ["ok"]


def test_load_misses_filters_by_since_and_taxonomy(opensre_home: Path) -> None:
    now = datetime.now(UTC)
    record_miss(
        _feedback_dict(timestamp=(now - timedelta(days=10)).isoformat(), alert_name="old"),
        taxonomy=MissTaxonomy.RETRIEVAL_GAP,
    )
    record_miss(
        _feedback_dict(
            timestamp=(now - timedelta(hours=2)).isoformat(), alert_name="fresh-retrieval"
        ),
        taxonomy=MissTaxonomy.RETRIEVAL_GAP,
    )
    record_miss(
        _feedback_dict(timestamp=(now - timedelta(hours=1)).isoformat(), alert_name="fresh-tool"),
        taxonomy=MissTaxonomy.TOOL_FAILURE,
    )

    recent = load_misses(since=now - timedelta(days=1))
    assert {r["alert_name"] for r in recent} == {"fresh-retrieval", "fresh-tool"}

    retrieval = load_misses(taxonomy=MissTaxonomy.RETRIEVAL_GAP)
    assert {r["alert_name"] for r in retrieval} == {"old", "fresh-retrieval"}


# ── recurrence + stats ────────────────────────────────────────────────────────


def test_compute_recurrence_groups_by_alert_and_taxonomy(opensre_home: Path) -> None:
    record_miss(_feedback_dict(alert_name="A"), taxonomy=MissTaxonomy.RETRIEVAL_GAP)
    record_miss(
        _feedback_dict(alert_name="A", feedback_id="fb-002"), taxonomy=MissTaxonomy.RETRIEVAL_GAP
    )
    record_miss(
        _feedback_dict(alert_name="A", feedback_id="fb-003"), taxonomy=MissTaxonomy.REASONING_GAP
    )
    record_miss(
        _feedback_dict(alert_name="B", feedback_id="fb-004"), taxonomy=MissTaxonomy.TOOL_FAILURE
    )

    rec = compute_recurrence(load_misses())
    assert rec[("A", "retrieval_gap")] == 2
    assert rec[("A", "reasoning_gap")] == 1
    assert rec[("B", "tool_failure")] == 1


def test_compute_stats_reports_totals_and_recurring(opensre_home: Path) -> None:
    record_miss(_feedback_dict(alert_name="A"), taxonomy=MissTaxonomy.RETRIEVAL_GAP)
    record_miss(
        _feedback_dict(alert_name="A", feedback_id="fb-002"), taxonomy=MissTaxonomy.RETRIEVAL_GAP
    )
    record_miss(
        _feedback_dict(alert_name="B", feedback_id="fb-003"), taxonomy=MissTaxonomy.TOOL_FAILURE
    )

    stats = compute_stats(load_misses())
    assert stats["total"] == 3
    assert stats["by_taxonomy"]["retrieval_gap"] == 2
    assert stats["by_taxonomy"]["tool_failure"] == 1
    assert stats["unique_alerts"] == 2
    assert stats["recurring"] == [("A", "retrieval_gap", 2)]


# ── filter_top_misses prioritises recurring pairs ─────────────────────────────


def test_filter_top_misses_dedupes_and_prefers_recurrence(opensre_home: Path) -> None:
    now = datetime.now(UTC)
    record_miss(
        _feedback_dict(alert_name="A", timestamp=(now - timedelta(days=3)).isoformat()),
        taxonomy=MissTaxonomy.RETRIEVAL_GAP,
    )
    record_miss(
        _feedback_dict(
            alert_name="A", feedback_id="fb-002", timestamp=(now - timedelta(days=1)).isoformat()
        ),
        taxonomy=MissTaxonomy.RETRIEVAL_GAP,
    )
    record_miss(
        _feedback_dict(alert_name="B", feedback_id="fb-003", timestamp=now.isoformat()),
        taxonomy=MissTaxonomy.TOOL_FAILURE,
    )

    top = filter_top_misses(load_misses(), top=10)

    pairs = [(r["alert_name"], r["taxonomy"]) for r in top]
    # Deduped to one per (alert, taxonomy)
    assert pairs == [("A", "retrieval_gap"), ("B", "tool_failure")]


def test_filter_top_misses_handles_zero_and_empty() -> None:
    assert filter_top_misses([], top=5) == []
    assert filter_top_misses([{"alert_name": "x"}], top=0) == []


# ── scenario conversion ──────────────────────────────────────────────────────


def test_to_benchmark_scenario_carries_rubric() -> None:
    miss = MissRecord(
        miss_id="m-1",
        run_id="r-1",
        alert_name="checkout-api 5xx",
        pipeline_name="checkout/api",
        severity="critical",
        timestamp="2026-06-02T10:00:00+00:00",
        taxonomy="retrieval_gap",
        taxonomy_detail="missed the canary deploy",
        root_cause="Bad deploy at 09:42",
        root_cause_category="deploy",
    )
    scenario = to_benchmark_scenario(miss)

    assert scenario["alert_name"] == "checkout-api 5xx"
    assert scenario["title"].startswith("[Regression]")
    assert scenario["pipeline_name"] == "checkout/api"
    assert scenario["severity"] == "critical"
    assert scenario["_meta"]["miss_id"] == "m-1"
    assert scenario["_meta"]["taxonomy"] == "retrieval_gap"

    # The rubric MUST live at commonAnnotations.scoring_points — that is
    # where extract_scoring_points reads it and where
    # strip_scoring_points_from_alert removes it before the agent sees the
    # alert. Anywhere else and either the judge misses the rubric or the
    # agent gets handed the answer.
    rubric = scenario["commonAnnotations"]["scoring_points"]
    assert rubric["expected_root_cause"] == "Bad deploy at 09:42"
    assert rubric["expected_category"] == "deploy"
    assert rubric["miss_notes"] == "missed the canary deploy"
    assert "scoring_points" not in scenario["_meta"]


def test_to_benchmark_scenario_is_strippable_for_blind_agent_runs() -> None:
    """Confirm the produced scenario is compatible with the existing
    strip_scoring_points_from_alert helper — i.e. the rubric ends up where
    the helper expects and is actually removed for non-evaluate runs."""
    from integrations.opensre import (
        extract_scoring_points,
        strip_scoring_points_from_alert,
    )

    miss = MissRecord(
        miss_id="m-1",
        alert_name="checkout-api 5xx",
        taxonomy="retrieval_gap",
        taxonomy_detail="missed the canary deploy",
        root_cause="Bad deploy at 09:42",
        root_cause_category="deploy",
    )
    scenario = to_benchmark_scenario(miss)

    # Judge can read the rubric.
    rubric_text = extract_scoring_points(scenario)
    assert "Bad deploy at 09:42" in rubric_text

    # Strip removes it cleanly — agent does not see the answer.
    blind = strip_scoring_points_from_alert(scenario)
    assert "scoring_points" not in blind["commonAnnotations"]
    assert extract_scoring_points(blind) == ""


def test_export_scenarios_handles_json_null_alert_name_and_taxonomy(tmp_path: Path) -> None:
    """A JSON null in misses.jsonl returns Python None from dict.get — the
    export path must not crash _slugify's re.sub on those values."""
    misses: list[MissRecord] = [
        MissRecord(
            miss_id="m-1",
            alert_name=None,  # type: ignore[typeddict-item]
            taxonomy=None,  # type: ignore[typeddict-item]
            timestamp="2026-06-02T10:00:00+00:00",
            root_cause="rc",
        ),
    ]
    out = tmp_path / "scenarios"

    written = export_scenarios(misses, out)

    assert len(written) == 1
    # Slug falls back to the index + "unknown" taxonomy rather than crashing.
    assert written[0].parent.name == "0001_miss-0001_unknown"


def test_export_scenarios_writes_per_case_directories(tmp_path: Path) -> None:
    misses: list[MissRecord] = [
        MissRecord(
            miss_id="m-1",
            alert_name="checkout 5xx",
            taxonomy="retrieval_gap",
            timestamp="2026-06-02T10:00:00+00:00",
            root_cause="rc",
        ),
        MissRecord(
            miss_id="m-2",
            alert_name="checkout 5xx",
            taxonomy="reasoning_gap",
            timestamp="2026-06-02T11:00:00+00:00",
            root_cause="rc2",
        ),
    ]
    out = tmp_path / "scenarios"

    written = export_scenarios(misses, out)

    assert len(written) == 2
    assert all(p.name == "alert.json" for p in written)
    parents = [p.parent.name for p in written]
    assert any("retrieval_gap" in name for name in parents)
    assert any("reasoning_gap" in name for name in parents)

    payload = json.loads(written[0].read_text())
    assert payload["_meta"]["taxonomy"] in {"retrieval_gap", "reasoning_gap"}


# ── parse_since input parsing ────────────────────────────────────────────────


def test_parse_since_accepts_relative_units() -> None:
    now = datetime.now(UTC)
    one_day_ago = parse_since("1d")
    assert (now - one_day_ago) < timedelta(seconds=5) + timedelta(days=1)
    assert (now - one_day_ago) > timedelta(hours=23, minutes=55)

    assert parse_since("12h") < now
    assert parse_since("2w") < now - timedelta(days=13)


def test_parse_since_accepts_iso_timestamp() -> None:
    parsed = parse_since("2026-06-02T10:00:00+00:00")
    assert parsed == datetime(2026, 6, 2, 10, 0, tzinfo=UTC)


def test_parse_since_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_since("")
    with pytest.raises(ValueError):
        parse_since("forever ago")
