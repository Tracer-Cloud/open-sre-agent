"""Determinism tests for observation artifact naming.

Verifies that:
- Two calls to write_observation with the same canonical payload produce
  byte-equal files with identical content-addressed names.
- The canonical filename matches ``^[0-9a-f]{12}\\.json$`` (no timestamp).
- latest.json is updated to point at the latest write.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.synthetic.rds_postgres.observations import RunObservation, write_observation


def _make_observation(tmp_path: Path, scenario_id: str = "000-test") -> RunObservation:
    """Build a minimal RunObservation with a deterministic canonical_report_payload."""
    canonical: dict[str, Any] = {
        "report_schema_version": "report_v2",
        "scoring_formula_version": "v2_gated_semantic",
        "status": "pass",
        "gates": {},
        "evidence": {
            "observed_sources": [],
            "required_sources": [],
            "missing_required_sources": [],
            "source_presence": {},
        },
        "trajectory": {"golden": [], "actual": [], "policy": None},
    }
    from tests.synthetic.rds_postgres.trajectory_policy import TrajectoryMetrics

    return RunObservation(
        report_schema_version="report_v2",
        scoring_formula_version="v2_gated_semantic",
        scenario_id=scenario_id,
        started_at=datetime.now(UTC).isoformat(),
        wall_time_s=1.0,
        suite="axis1",
        backend="FixtureGrafanaBackend",
        score={"passed": True},
        trajectory=TrajectoryMetrics(
            flat_actions=[],
            actions_per_loop=[],
            strict_match=None,
            lcs_ratio=None,
            edit_distance=None,
            coverage=None,
            extra_actions=[],
            missing_actions=[],
            redundancy_count=0,
            loops_used=0,
            max_loops=None,
            loop_calibration_ok=None,
            failed_action_count=0,
        ),
        evaluated_golden_actions=[],
        trajectory_policy=None,
        trajectory_policy_version="default_v1",
        reasoning=None,
        reasoning_status="not_captured",
        observed_evidence_sources=[],
        required_evidence_sources=[],
        missing_required_evidence_sources=[],
        evidence_source_coverage={},
        canonical_report_payload=canonical,
        final_state_digest="abc123",
    )


def test_repeated_writes_produce_byte_equal_canonical_payloads(tmp_path: Path) -> None:
    """Two writes of the same observation must produce byte-identical canonical files."""
    obs = _make_observation(tmp_path)

    dir_a = tmp_path / "run_a"
    dir_b = tmp_path / "run_b"

    path_a = write_observation(obs, dir_a)
    path_b = write_observation(obs, dir_b)

    assert path_a.name == path_b.name, (
        f"Canonical filenames differ: {path_a.name!r} vs {path_b.name!r}"
    )

    content_a = path_a.read_bytes()
    content_b = path_b.read_bytes()

    # Canonical report payload must be byte-equal (observation_path will differ
    # since it includes the dir path — compare the canonical_report_payload key only).
    payload_a = json.loads(content_a)["canonical_report_payload"]
    payload_b = json.loads(content_b)["canonical_report_payload"]

    # Strip observation_path (includes tmp dir which differs between runs)
    payload_a.pop("observation_path", None)
    payload_b.pop("observation_path", None)

    assert payload_a == payload_b, (
        "canonical_report_payload differs between two writes of the same observation"
    )


def test_canonical_filename_is_content_addressed(tmp_path: Path) -> None:
    """Canonical filename must be a 12-hex-char digest with no timestamp characters."""
    obs = _make_observation(tmp_path)
    path = write_observation(obs, tmp_path)

    name = path.name
    assert re.fullmatch(r"[0-9a-f]{12}\.json", name), (
        f"Canonical filename {name!r} does not match '^[0-9a-f]{{12}}\\.json$'"
    )
    # Ensure no timestamp characters (colons or 'Z') appear in the filename
    assert ":" not in name, f"Filename contains ':' (timestamp pattern): {name!r}"
    assert "Z" not in name, f"Filename contains 'Z' (timestamp pattern): {name!r}"


def test_latest_json_is_updated(tmp_path: Path) -> None:
    """latest.json must exist and contain the same payload as the canonical file."""
    obs = _make_observation(tmp_path)
    path = write_observation(obs, tmp_path)

    scenario_dir = tmp_path / obs.scenario_id
    latest = scenario_dir / "latest.json"
    assert latest.exists(), "latest.json was not written"

    canonical_content = json.loads(path.read_text(encoding="utf-8"))
    latest_content = json.loads(latest.read_text(encoding="utf-8"))
    assert canonical_content == latest_content, "latest.json content differs from canonical file"


def test_different_payloads_produce_different_filenames(tmp_path: Path) -> None:
    """Two observations with different canonical payloads must get different filenames."""
    obs_a = _make_observation(tmp_path, scenario_id="000-test")
    from dataclasses import replace

    # Mutate the canonical_report_payload to produce a different digest
    new_canonical = dict(obs_a.canonical_report_payload)
    new_canonical["status"] = "fail"
    obs_b = replace(obs_a, canonical_report_payload=new_canonical)

    path_a = write_observation(obs_a, tmp_path)
    path_b = write_observation(obs_b, tmp_path)

    assert path_a.name != path_b.name, (
        "Different canonical payloads should produce different filenames"
    )
