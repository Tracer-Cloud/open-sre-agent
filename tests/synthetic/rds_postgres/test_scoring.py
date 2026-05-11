"""Tests for the pure scoring module.

The key invariant verified here: importing scoring.py must NOT pull in any
``app.*`` modules (and therefore not LangGraph or any heavy runtime deps).
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from tests.synthetic.rds_postgres.scenario_loader import SUITE_DIR, load_all_scenarios


def test_scoring_module_imports_without_app_pipeline() -> None:
    """scoring.py must be importable without touching app.pipeline."""
    # Force the import to happen in this test's process; if scoring.py was
    # already imported earlier that's fine — we just check the invariant holds.
    from tests.synthetic.rds_postgres.scoring import score_result  # noqa: F401

    app_pipeline_modules = [k for k in sys.modules if k.startswith("app.pipeline")]
    assert app_pipeline_modules == [], (
        f"scoring.py transitively imported app.pipeline modules: {app_pipeline_modules}"
    )


# ---------------------------------------------------------------------------
# Keyword matching unit tests (relocated from test_suite.py)
# ---------------------------------------------------------------------------


def _normalized(text: str) -> str:
    from tests.synthetic.rds_postgres.scoring import _normalize_text

    return _normalize_text(text)


def test_normalize_text_lowercases_and_collapses_whitespace() -> None:
    from tests.synthetic.rds_postgres.scoring import _normalize_text

    assert _normalize_text("  Hello   World  ") == "hello world"
    assert _normalize_text("CPUUtilization") == "cpuutilization"


def test_normalize_query_token_replaces_separators() -> None:
    from tests.synthetic.rds_postgres.scoring import _normalize_query_token

    assert _normalize_query_token("write-heavy workload") == "write_heavy_workload"
    assert _normalize_query_token("CPU Utilization") == "cpu_utilization"


def test_keyword_match_exact_phrase() -> None:
    from tests.synthetic.rds_postgres.scoring import _keyword_match_details

    matched, mode, alias = _keyword_match_details("replication lag detected", "replication lag")
    assert matched is True
    assert mode == "exact_phrase"
    assert alias is None


def test_keyword_match_alias_lookup_replication_lag() -> None:
    from tests.synthetic.rds_postgres.scoring import _keyword_match_details

    matched, mode, alias = _keyword_match_details("replica lag is high", "replicationlag")
    assert matched is True
    assert mode == "alias_lookup"
    assert alias == "replica lag"


def test_keyword_match_alias_lookup_replicalag_token() -> None:
    from tests.synthetic.rds_postgres.scoring import _keyword_match_details

    matched, mode, alias = _keyword_match_details("replicalag increased sharply", "replication lag")
    assert matched is True
    assert mode == "alias_lookup"
    assert alias == "replicalag"


def test_keyword_match_alias_lookup_causally_independent() -> None:
    from tests.synthetic.rds_postgres.scoring import _keyword_match_details

    matched, mode, alias = _keyword_match_details(
        "the cpu spike is a red herring", "causallyindependent"
    )
    assert matched is True
    assert mode == "alias_lookup"


def test_keyword_match_token_subset() -> None:
    from tests.synthetic.rds_postgres.scoring import _keyword_match_details

    matched, mode, _ = _keyword_match_details("cpu saturation detected on host", "cpu saturation")
    assert matched is True
    assert mode in ("exact_phrase", "token_subset")


def test_keyword_match_no_match() -> None:
    from tests.synthetic.rds_postgres.scoring import _keyword_match_details

    matched, mode, _ = _keyword_match_details("everything is fine", "replication lag")
    assert matched is False
    assert mode == "none"


def test_score_result_uses_semantic_keyword_matching_for_write_heavy_workload() -> None:
    """Semantic alias 'write heavy workload' must satisfy 'write-heavyworkload' keyword."""
    from tests.synthetic.rds_postgres.scenario_loader import SUITE_DIR, load_all_scenarios
    from tests.synthetic.rds_postgres.scoring import score_result

    fixtures = load_all_scenarios(SUITE_DIR)
    write_heavy_fixture = next((f for f in fixtures if "write" in f.scenario_id.lower()), None)
    if write_heavy_fixture is None:
        pytest.skip("no write-heavy fixture in current suite")

    final_state: dict[str, Any] = {
        "root_cause": "write heavy workload causing checkpoint pressure",
        "root_cause_category": write_heavy_fixture.answer_key.root_cause_category,
        "evidence": {
            "aws_cloudwatch_metrics": {
                "metrics": [{"metric_name": "WriteIOPS"}],
                "observations": ["write heavy workload"],
            }
        },
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": ["write heavy workload"],
        "report": "",
        "executed_hypotheses": [],
        "investigation_loop_count": 0,
    }
    score = score_result(write_heavy_fixture, final_state)
    assert "write-heavyworkload" not in score.semantic_missing_keywords, (
        "write-heavyworkload alias should match via 'write heavy workload'"
    )


# ---------------------------------------------------------------------------
# score_result basic contract
# ---------------------------------------------------------------------------


def test_score_result_returns_scenario_score_type() -> None:
    from tests.synthetic.rds_postgres.scoring import ScenarioScore, score_result

    fixtures = load_all_scenarios(SUITE_DIR)
    fixture = fixtures[0]
    final_state: dict[str, Any] = {
        "root_cause": "",
        "root_cause_category": "unknown",
        "evidence": {},
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "report": "",
        "executed_hypotheses": [],
        "investigation_loop_count": 0,
    }
    result = score_result(fixture, final_state)
    assert isinstance(result, ScenarioScore)
    assert result.scenario_id == fixture.scenario_id


def test_score_result_all_required_gates_present() -> None:
    """Every required gate name must be present in a scored result."""
    from tests.synthetic.rds_postgres.scoring import _REQUIRED_GATE_NAMES, score_result

    fixtures = load_all_scenarios(SUITE_DIR)
    fixture = fixtures[0]
    final_state: dict[str, Any] = {
        "root_cause": "",
        "root_cause_category": "unknown",
        "evidence": {},
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "report": "",
        "executed_hypotheses": [],
        "investigation_loop_count": 0,
    }
    result = score_result(fixture, final_state)
    # trajectory_policy is set by _apply_trajectory_policy_to_score in run_suite,
    # not by score_result itself — skip it here.
    scoring_gate_names = _REQUIRED_GATE_NAMES - {"trajectory_policy"}
    missing = scoring_gate_names - set(result.gates)
    assert not missing, f"Missing required gates in score_result output: {missing}"
