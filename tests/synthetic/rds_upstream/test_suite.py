"""Fixture and scoring tests for the rds_upstream synthetic suite (#1437)."""

from __future__ import annotations

import pytest

from tests.synthetic.rds_upstream.scenario_loader import load_all_scenarios
from tests.synthetic.schemas import VALID_EVIDENCE_SOURCES

pytestmark = pytest.mark.synthetic


def test_load_all_upstream_scenarios() -> None:
    fixtures = load_all_scenarios()
    scenario_ids = [fixture.scenario_id for fixture in fixtures]
    assert "001-request-burst-ec2-app-tier" in scenario_ids


def test_upstream_scenario_metadata_and_evidence() -> None:
    for fixture in load_all_scenarios():
        meta = fixture.metadata
        assert meta.schema_version
        assert meta.available_evidence
        unknown = set(meta.available_evidence) - VALID_EVIDENCE_SOURCES
        assert not unknown, f"{fixture.scenario_id}: unknown evidence {unknown}"
        evidence_dict = fixture.evidence.as_dict()
        assert set(evidence_dict.keys()) == set(meta.available_evidence)


def test_request_burst_answer_key_expects_cross_system_evidence() -> None:
    from tests.synthetic.rds_upstream.scenario_loader import SUITE_DIR, load_scenario

    fixture = load_scenario(SUITE_DIR / "001-request-burst-ec2-app-tier")
    assert "ec2_instances_by_tag" in fixture.answer_key.required_evidence_sources
    assert "elb_target_health" in fixture.answer_key.required_evidence_sources
    assert fixture.answer_key.root_cause_category == "application_tier_load_spike"
