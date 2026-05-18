from __future__ import annotations

from dataclasses import replace

import pytest

from tests.synthetic.hermes_rca.hermes_schemas import (
    VALID_HERMES_EVIDENCE_SOURCES,
    VALID_HERMES_FAILURE_MODES,
    validate_hermes_answer_key,
)
from tests.synthetic.hermes_rca.run_suite import score_result
from tests.synthetic.hermes_rca.scenario_loader import SUITE_DIR, load_all_scenarios
from tests.synthetic.mock_hermes_backend.backend import FixtureHermesBackend

pytestmark = [pytest.mark.synthetic, pytest.mark.hermes]


def test_load_all_scenarios_discovers_cases() -> None:
    fixtures = load_all_scenarios(SUITE_DIR)
    scenario_ids = [fixture.scenario_id for fixture in fixtures]
    assert "000-healthy" in scenario_ids
    assert "010-compression-invalid-tool-ordering" in scenario_ids
    assert "014-tui-compression-ghost-session" in scenario_ids


def test_scenario_metadata_uses_valid_vocab() -> None:
    fixtures = load_all_scenarios(SUITE_DIR)
    assert fixtures, "no Hermes RCA scenarios discovered"

    for fixture in fixtures:
        assert fixture.metadata.failure_mode in VALID_HERMES_FAILURE_MODES
        assert fixture.metadata.available_evidence
        unknown = set(fixture.metadata.available_evidence) - VALID_HERMES_EVIDENCE_SOURCES
        assert not unknown, f"{fixture.scenario_id}: unknown evidence sources {unknown}"


def test_scenario_evidence_matches_declared_available_evidence() -> None:
    fixtures = load_all_scenarios(SUITE_DIR)

    for fixture in fixtures:
        evidence_keys = set(fixture.evidence.as_dict().keys())
        declared = set(fixture.metadata.available_evidence)
        assert evidence_keys == declared, (
            f"{fixture.scenario_id}: evidence keys {evidence_keys} do not match "
            f"available_evidence {declared}"
        )


def test_fixture_backend_hang_detection_uses_frozen_now_ts() -> None:
    fixture = next(
        scenario
        for scenario in load_all_scenarios(SUITE_DIR)
        if scenario.scenario_id == "011-cli-hang-no-interrupt-drain"
    )
    backend = FixtureHermesBackend(fixture, hang_threshold_s=120)
    runtime = backend.get_runtime_state()

    assert runtime["available"] is True
    assert runtime["is_blocked"] is True
    assert runtime["frozen_now_ts"]
    assert runtime["last_progress_ts"]


def test_fixture_backend_delivery_hang_shape() -> None:
    fixture = next(
        scenario
        for scenario in load_all_scenarios(SUITE_DIR)
        if scenario.scenario_id == "012-cron-hang-post-output"
    )
    backend = FixtureHermesBackend(fixture)
    cron_state = backend.get_cron_state()

    assert cron_state["available"] is True
    assert cron_state["last_run"]["delivery_status"] == "never_started"


def test_compression_ordering_fixture_includes_pre_and_post_snapshots() -> None:
    fixture = next(
        scenario
        for scenario in load_all_scenarios(SUITE_DIR)
        if scenario.scenario_id == "010-compression-invalid-tool-ordering"
    )
    backend = FixtureHermesBackend(fixture)
    history = backend.get_message_history()

    snapshots = history.get("snapshots")
    assert isinstance(snapshots, dict)

    pre_messages = snapshots.get("pre_compression")
    post_messages = snapshots.get("post_compression")
    assert isinstance(pre_messages, list)
    assert isinstance(post_messages, list)

    assert pre_messages[1]["role"] == "tool_call"
    assert pre_messages[2]["role"] == "tool"
    assert post_messages[1]["role"] == "tool"
    assert post_messages[2]["role"] == "tool_call"


def test_answer_key_category_must_be_valid_taxonomy_value() -> None:
    with pytest.raises(ValueError, match="unknown root_cause_category"):
        validate_hermes_answer_key(
            {
                "root_cause_category": "not_a_real_category",
                "required_keywords": ["x"],
                "model_response": "y",
            }
        )


def test_answer_key_rejects_conflicting_forbidden_category() -> None:
    with pytest.raises(ValueError, match="cannot also appear"):
        validate_hermes_answer_key(
            {
                "root_cause_category": "agent_hang",
                "required_keywords": ["x"],
                "model_response": "y",
                "forbidden_categories": ["agent_hang"],
            }
        )


def test_forbidden_category_check_precedes_wrong_category_reason() -> None:
    fixture = next(
        scenario
        for scenario in load_all_scenarios(SUITE_DIR)
        if scenario.scenario_id == "012-cron-hang-post-output"
    )
    fixture = replace(
        fixture,
        answer_key=replace(
            fixture.answer_key,
            forbidden_categories=["agent_hang"],
        ),
    )
    score = score_result(
        fixture,
        {
            "root_cause_category": "agent_hang",
            "root_cause": "",
            "report": "",
            "problem_md": "",
            "validated_claims": [],
        },
    )
    assert score.passed is False
    assert score.failure_reason == "forbidden category emitted: agent_hang"


def test_forbidden_keywords_fail_score() -> None:
    fixture = next(
        scenario
        for scenario in load_all_scenarios(SUITE_DIR)
        if scenario.scenario_id == "000-healthy"
    )
    fixture = replace(
        fixture,
        answer_key=replace(
            fixture.answer_key,
            forbidden_keywords=["panic"],
        ),
    )
    score = score_result(
        fixture,
        {
            "root_cause_category": "healthy",
            "root_cause": "service panic observed",
            "report": "",
            "problem_md": "",
            "validated_claims": [],
        },
    )
    assert score.passed is False
    assert score.forbidden_keywords_present == ["panic"]
