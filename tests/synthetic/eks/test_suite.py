from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.config import has_credentials_for_active_llm_provider
from tests.synthetic.eks.run_suite import run_scenario, score_result
from tests.synthetic.eks.scenario_loader import (
    SUITE_DIR,
    load_all_scenarios,
    load_scenario,
)
from tests.synthetic.k8s_schemas import VALID_K8S_EVIDENCE_SOURCES
from tests.synthetic.mock_datadog_backend.backend import FixtureDatadogBackend
from tests.synthetic.mock_eks_backend.backend import FixtureEKSBackend

# Synthetic E2E uses fixture EKS/Datadog backends; many tool "calls" hit mocks, not real APIs.
# This gate only reflects whether the *LLM* can authenticate (the reason we skip when keys are absent).
_SYNTHETIC_SKIP_LLM = (
    "SKIPPED: missing API key for LLM_PROVIDER "
    "(suite uses mock EKS/Datadog backends; configure the key for your selected provider)"
)

# ---------------------------------------------------------------------------
# Loader and fixture validation
# ---------------------------------------------------------------------------


def test_load_all_scenarios_reads_benchmark_cases() -> None:
    fixtures = load_all_scenarios()

    scenario_ids = [fixture.scenario_id for fixture in fixtures]
    assert "000-healthy" in scenario_ids


def test_scenario_metadata_is_valid() -> None:
    fixtures = load_all_scenarios()

    for fixture in fixtures:
        meta = fixture.metadata
        assert meta.schema_version, f"{fixture.scenario_id}: schema_version must be set"
        assert meta.engine, f"{fixture.scenario_id}: engine must be set"
        assert meta.cluster_name, f"{fixture.scenario_id}: cluster_name must be set"
        assert meta.namespace, f"{fixture.scenario_id}: namespace must be set"
        assert meta.workload_type, f"{fixture.scenario_id}: workload_type must be set"
        assert meta.workload_name, f"{fixture.scenario_id}: workload_name must be set"
        assert meta.failure_mode, f"{fixture.scenario_id}: failure_mode must be set"
        assert meta.region, f"{fixture.scenario_id}: region must be set"
        assert meta.available_evidence, (
            f"{fixture.scenario_id}: available_evidence must not be empty"
        )
        unknown = set(meta.available_evidence) - VALID_K8S_EVIDENCE_SOURCES
        assert not unknown, f"{fixture.scenario_id}: unknown evidence sources {unknown}"


def test_scenario_evidence_matches_available_evidence() -> None:
    fixtures = load_all_scenarios()

    for fixture in fixtures:
        evidence_dict = fixture.evidence.as_dict()
        assert set(evidence_dict.keys()) == set(fixture.metadata.available_evidence), (
            f"{fixture.scenario_id}: evidence keys {set(evidence_dict.keys())} "
            f"do not match available_evidence {fixture.metadata.available_evidence}"
        )


# ---------------------------------------------------------------------------
# Mock backend shape tests
# ---------------------------------------------------------------------------


class TestMockBackendShapes:
    """Verify each mock backend method returns the exact envelope the real tool would."""

    @pytest.fixture
    def placeholder(self):
        return load_scenario(SUITE_DIR / "000-healthy")

    def test_eks_list_pods_shape(self, placeholder) -> None:
        backend = FixtureEKSBackend(placeholder)
        result = backend.list_pods(cluster_name="override", namespace="override-ns")
        assert result["source"] == "eks"
        assert result["available"] is True
        assert result["error"] is None
        assert "total_pods" in result
        assert "pods" in result
        assert "failing_pods" in result
        assert "high_restart_pods" in result
        assert result["cluster_name"] == "override"
        assert result["namespace"] == "override-ns"

    def test_eks_list_pods_falls_back_to_metadata(self, placeholder) -> None:
        backend = FixtureEKSBackend(placeholder)
        result = backend.list_pods()
        assert result["cluster_name"] == placeholder.metadata.cluster_name
        assert result["namespace"] == placeholder.metadata.namespace

    def test_eks_get_events_shape(self, placeholder) -> None:
        backend = FixtureEKSBackend(placeholder)
        result = backend.get_events()
        assert result["source"] == "eks"
        assert result["available"] is True
        assert result["error"] is None
        assert "warning_events" in result
        assert "total_warning_count" in result
        assert result["total_warning_count"] == len(result["warning_events"])

    def test_eks_list_deployments_shape(self, placeholder) -> None:
        backend = FixtureEKSBackend(placeholder)
        result = backend.list_deployments()
        assert result["source"] == "eks"
        assert result["available"] is True
        assert "deployments" in result
        assert "degraded_deployments" in result
        assert "total_deployments" in result
        for deployment in result["deployments"]:
            for field in (
                "name",
                "namespace",
                "desired",
                "ready",
                "available",
                "unavailable",
                "degraded",
            ):
                assert field in deployment, f"missing field {field}"

    def test_eks_node_health_shape(self, placeholder) -> None:
        backend = FixtureEKSBackend(placeholder)
        result = backend.get_node_health()
        assert result["source"] == "eks"
        assert result["available"] is True
        assert "nodes" in result
        assert "not_ready_count" in result
        assert "total_nodes" in result
        assert result["not_ready_count"] == 0

    def test_eks_missing_evidence_raises(self, placeholder) -> None:
        """Calling a method whose evidence source wasn't declared raises ValueError."""
        backend = FixtureEKSBackend(placeholder)
        # The placeholder deliberately omits eks_pod_logs; calling get_pod_logs must fail.
        assert "eks_pod_logs" not in placeholder.metadata.available_evidence
        with pytest.raises(ValueError, match="eks_pod_logs"):
            backend.get_pod_logs(pod_name="payments-api-7f9dd-x7gr9")

    def test_datadog_query_logs_shape(self, placeholder) -> None:
        backend = FixtureDatadogBackend(placeholder)
        result = backend.query_logs(query="service:payments-api")
        assert result["source"] == "datadog_logs"
        assert result["available"] is True
        assert "logs" in result
        assert "error_logs" in result
        assert result["query"] == "service:payments-api"

    def test_datadog_query_monitors_shape(self, placeholder) -> None:
        backend = FixtureDatadogBackend(placeholder)
        result = backend.query_monitors()
        assert result["source"] == "datadog_monitors"
        assert result["available"] is True
        assert "monitors" in result
        assert "total" in result


# ---------------------------------------------------------------------------
# Scorer unit tests (no agent run required)
# ---------------------------------------------------------------------------


class TestScorer:
    """Feed canned final_state dicts into the scorer and verify the result."""

    @pytest.fixture
    def placeholder(self):
        return load_scenario(SUITE_DIR / "000-healthy")

    def test_matching_root_cause_passes(self, placeholder) -> None:
        final_state = {
            "root_cause": "The Kubernetes workload is operating within normal parameters. "
            "No failure detected across pods, events, deployments, or monitors.",
            "root_cause_category": "healthy",
            "validated_claims": [],
            "non_validated_claims": [],
            "causal_chain": [],
            "evidence": {},
            "executed_hypotheses": [],
            "investigation_loop_count": 0,
        }
        score = score_result(placeholder, final_state)
        assert score.passed is True, score.failure_reason
        assert score.actual_category == "healthy"
        assert not score.missing_keywords

    def test_wrong_category_fails(self, placeholder) -> None:
        final_state = {
            "root_cause": "Some narrative.",
            "root_cause_category": "crashloop_backoff",
            "validated_claims": [],
            "non_validated_claims": [],
            "causal_chain": [],
            "evidence": {},
            "executed_hypotheses": [],
            "investigation_loop_count": 0,
        }
        score = score_result(placeholder, final_state)
        assert score.passed is False
        assert "wrong category" in score.failure_reason

    def test_missing_keyword_fails(self, placeholder) -> None:
        final_state = {
            "root_cause": "nothing important to report",
            "root_cause_category": "healthy",
            "validated_claims": [],
            "non_validated_claims": [],
            "causal_chain": [],
            "evidence": {},
            "executed_hypotheses": [],
            "investigation_loop_count": 0,
        }
        score = score_result(placeholder, final_state)
        assert score.passed is False
        assert "missing required keywords" in score.failure_reason


# ---------------------------------------------------------------------------
# Parametrized LLM runs — gated behind scenarios existing at each difficulty level.
# The placeholder 000-healthy uses scenario_difficulty: 0, so these collections
# are empty until scenarios #261+ land with real difficulty-tiered content.
# ---------------------------------------------------------------------------


_ALL_SCENARIOS = load_all_scenarios()
_LLM_ATTEMPTS = 2


def _by_difficulty(level: int) -> list:
    return [f for f in _ALL_SCENARIOS if f.metadata.scenario_difficulty == level]


def _should_assert_trajectory(fixture, actual_category: str) -> bool:
    """Keep trajectory assertions for lower-difficulty scenarios only.

    The higher-difficulty suites intentionally exercise ambiguous prompts and
    alternative evidence paths. In practice, real LLMs can still land on the
    correct diagnosis while taking a different-but-reasonable route, so we only
    gate trajectory exactness for the lower-difficulty benchmark tiers.
    """

    return fixture.metadata.scenario_difficulty <= 2 and actual_category != "healthy"


def _run_scenario_test(fixture) -> None:
    """Run scenario with real LLM and mock backends, then assert scoring."""
    if not has_credentials_for_active_llm_provider():
        pytest.skip(_SYNTHETIC_SKIP_LLM)

    failures: list[str] = []
    for attempt in range(1, _LLM_ATTEMPTS + 1):
        final_state, score = run_scenario(fixture, use_mock_backends=True)

        try:
            assert final_state["root_cause"]
            assert score.passed is True, (
                f"{fixture.scenario_id} FAILED: {score.failure_reason}\n"
                f"  actual_category={score.actual_category!r}  "
                f"  missing_keywords={score.missing_keywords}"
            )

            if (
                _should_assert_trajectory(fixture, score.actual_category)
                and score.trajectory is not None
            ):
                assert score.trajectory.sequencing_ok, (
                    f"{fixture.scenario_id} TRAJECTORY FAIL: "
                    f"sequencing={score.trajectory.sequencing_ok} "
                    f"calibration={score.trajectory.calibration_ok}\n"
                    f"  expected={score.trajectory.expected_sequence}\n"
                    f"  actual={score.trajectory.actual_sequence}"
                )
            return
        except AssertionError as exc:
            failures.append(f"attempt {attempt}/{_LLM_ATTEMPTS}: {exc}")

    raise AssertionError("\n\n".join(failures))


_LEVEL1_SCENARIOS = _by_difficulty(1)
_LEVEL2_SCENARIOS = _by_difficulty(2)
_LEVEL3_SCENARIOS = _by_difficulty(3)
_LEVEL4_SCENARIOS = _by_difficulty(4)


@pytest.mark.synthetic
@pytest.mark.skipif(not _LEVEL1_SCENARIOS, reason="no Level 1 K8s scenarios yet")
@pytest.mark.parametrize(
    "fixture", _LEVEL1_SCENARIOS or [None], ids=lambda f: f.scenario_id if f else "none"
)
def test_level1_scenario(fixture) -> None:
    """Level 1 — single dominant signal, all evidence consistent."""
    _run_scenario_test(fixture)


@pytest.mark.synthetic
@pytest.mark.skipif(not _LEVEL2_SCENARIOS, reason="no Level 2 K8s scenarios yet")
@pytest.mark.parametrize(
    "fixture", _LEVEL2_SCENARIOS or [None], ids=lambda f: f.scenario_id if f else "none"
)
def test_level2_scenario(fixture) -> None:
    """Level 2 — one confounder present, second evidence source needed to rule it out."""
    _run_scenario_test(fixture)


@pytest.mark.synthetic
@pytest.mark.skipif(not _LEVEL3_SCENARIOS, reason="no Level 3 K8s scenarios yet")
@pytest.mark.parametrize(
    "fixture", _LEVEL3_SCENARIOS or [None], ids=lambda f: f.scenario_id if f else "none"
)
def test_level3_scenario(fixture) -> None:
    """Level 3 — absent or indirect evidence, key signal missing."""
    _run_scenario_test(fixture)


@pytest.mark.synthetic
@pytest.mark.skipif(not _LEVEL4_SCENARIOS, reason="no Level 4 K8s scenarios yet")
@pytest.mark.parametrize(
    "fixture", _LEVEL4_SCENARIOS or [None], ids=lambda f: f.scenario_id if f else "none"
)
def test_level4_scenario(fixture) -> None:
    """Level 4 — compositional fault, two failure modes causally linked."""
    _run_scenario_test(fixture)


# ---------------------------------------------------------------------------
# Scenario inheritance unit tests
# ---------------------------------------------------------------------------


def _write_minimal_answer_yml(scenario_dir: Path) -> None:
    (scenario_dir / "answer.yml").write_text(
        textwrap.dedent("""\
        root_cause_category: test_category
        required_keywords:
          - test_keyword
        model_response: "Test model response."
    """)
    )


class TestScenarioInheritance:
    """Verify base-inheritance and evidence-file fallback in scenario_loader."""

    def test_metadata_inherited_from_base(self) -> None:
        """Scenario with base: 000-healthy inherits metadata fields it omits."""
        real_dir = SUITE_DIR / "999-test-inherit"
        real_dir.mkdir(exist_ok=True)
        try:
            (real_dir / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 000-healthy
                scenario_id: 999-test-inherit
                failure_mode: crashloop_backoff
                severity: critical
            """)
            )
            _write_minimal_answer_yml(real_dir)

            fixture = load_scenario(real_dir)

            assert fixture.metadata.scenario_id == "999-test-inherit"
            assert fixture.metadata.failure_mode == "crashloop_backoff"
            assert fixture.metadata.severity == "critical"
            assert fixture.metadata.engine == "eks"
            assert fixture.metadata.cluster_name == "payments-prod-eks"
            assert fixture.metadata.namespace == "payments"
            assert fixture.metadata.workload_type == "deployment"
            assert fixture.metadata.workload_name == "payments-api"
            assert fixture.metadata.region == "us-east-1"
            assert fixture.metadata.schema_version == "1.0"
            assert "eks_pods" in fixture.metadata.available_evidence
        finally:
            for f in real_dir.iterdir():
                f.unlink()
            real_dir.rmdir()

    def test_evidence_falls_back_to_base(self) -> None:
        """Scenario without evidence files loads them from the base."""
        real_dir = SUITE_DIR / "999-test-fallback"
        real_dir.mkdir(exist_ok=True)
        try:
            (real_dir / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 000-healthy
                scenario_id: 999-test-fallback
                failure_mode: healthy
                severity: info
            """)
            )
            _write_minimal_answer_yml(real_dir)

            fixture = load_scenario(real_dir)

            assert fixture.evidence.eks_pods is not None
            assert fixture.evidence.eks_events is not None
            assert fixture.evidence.datadog_logs is not None

            assert fixture.alert["state"] == "normal"
            assert "payments" in fixture.alert["title"].lower()
        finally:
            for f in real_dir.iterdir():
                f.unlink()
            real_dir.rmdir()

    def test_local_evidence_overrides_base(self) -> None:
        """Scenario with its own evidence file uses it instead of the base's."""
        real_dir = SUITE_DIR / "999-test-override"
        real_dir.mkdir(exist_ok=True)
        try:
            (real_dir / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 000-healthy
                scenario_id: 999-test-override
                failure_mode: healthy
                severity: info
            """)
            )
            _write_minimal_answer_yml(real_dir)

            custom_events = {
                "warning_events": [
                    {
                        "namespace": "payments",
                        "reason": "TestEvent",
                        "message": "Custom test event",
                        "type": "Warning",
                        "count": 1,
                        "involved_object": "Pod/custom-test",
                        "first_time": "2026-04-01T00:00:00Z",
                        "last_time": "2026-04-01T00:00:00Z",
                    }
                ]
            }
            (real_dir / "eks_events.json").write_text(json.dumps(custom_events))

            fixture = load_scenario(real_dir)

            assert fixture.evidence.eks_events is not None
            assert len(fixture.evidence.eks_events["warning_events"]) == 1
            assert (
                fixture.evidence.eks_events["warning_events"][0]["message"] == "Custom test event"
            )
        finally:
            for f in real_dir.iterdir():
                f.unlink()
            real_dir.rmdir()

    def test_chained_inheritance_rejected(self) -> None:
        """Declaring base on a scenario that itself has a base raises ValueError."""
        real_dir_a = SUITE_DIR / "999-test-chain-a"
        real_dir_b = SUITE_DIR / "999-test-chain-b"
        real_dir_a.mkdir(exist_ok=True)
        real_dir_b.mkdir(exist_ok=True)
        try:
            (real_dir_a / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 000-healthy
                scenario_id: 999-test-chain-a
                failure_mode: healthy
                severity: info
            """)
            )
            (real_dir_b / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 999-test-chain-a
                scenario_id: 999-test-chain-b
                failure_mode: healthy
                severity: info
            """)
            )
            _write_minimal_answer_yml(real_dir_b)

            with pytest.raises(ValueError, match="Chained inheritance is not supported"):
                load_scenario(real_dir_b)
        finally:
            for d in (real_dir_a, real_dir_b):
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()

    def test_missing_base_raises(self) -> None:
        """Referencing a non-existent base scenario raises ValueError."""
        real_dir = SUITE_DIR / "999-test-missing-base"
        real_dir.mkdir(exist_ok=True)
        try:
            (real_dir / "scenario.yml").write_text(
                textwrap.dedent("""\
                base: 999-nonexistent
                scenario_id: 999-test-missing-base
                failure_mode: healthy
                severity: info
            """)
            )
            _write_minimal_answer_yml(real_dir)

            with pytest.raises(ValueError, match="Base scenario '999-nonexistent' not found"):
                load_scenario(real_dir)
        finally:
            for f in real_dir.iterdir():
                f.unlink()
            real_dir.rmdir()

    def test_no_base_works_unchanged(self) -> None:
        """Scenarios without a base field still load normally."""
        fixture = load_scenario(SUITE_DIR / "000-healthy")
        assert fixture.metadata.scenario_id == "000-healthy"
        assert fixture.metadata.failure_mode == "healthy"
        assert fixture.evidence.eks_pods is not None
