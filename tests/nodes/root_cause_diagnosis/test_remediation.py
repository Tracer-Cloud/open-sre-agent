"""Tests for remediation_templates and parse_root_cause REMEDIATION_STEPS parsing."""

from app.nodes.root_cause_diagnosis.remediation_templates import get_template_steps
from app.services.llm_client import parse_root_cause


class TestGetTemplateSteps:
    def test_resource_exhaustion_no_sources(self):
        steps = get_template_steps("resource_exhaustion", {})
        assert len(steps) >= 3
        assert all(isinstance(s, str) for s in steps)
        # source-gated steps excluded
        assert not any("Grafana" in s for s in steps)
        assert not any("Datadog" in s for s in steps)
        assert not any("EKS" in s for s in steps)

    def test_resource_exhaustion_with_grafana_and_eks(self):
        steps = get_template_steps("resource_exhaustion", {"grafana": {}, "eks": {}})
        assert any("Grafana" in s for s in steps)
        assert any("EKS" in s for s in steps)
        assert not any("Datadog" in s for s in steps)

    def test_healthy_returns_empty(self):
        assert get_template_steps("healthy", {}) == []

    def test_unknown_category_falls_back_to_unknown(self):
        steps = get_template_steps("nonexistent_category", {})
        unknown_steps = get_template_steps("unknown", {})
        assert steps == unknown_steps
        assert len(steps) > 0


class TestParseRootCauseRemediationSteps:
    def test_remediation_steps_parsed(self):
        response = """ROOT_CAUSE:
Memory limit exceeded.

ROOT_CAUSE_CATEGORY:
resource_exhaustion

VALIDATED_CLAIMS:
- Pod exited with code 137 [evidence: eks]

NON_VALIDATED_CLAIMS:
- Traffic spike may have caused burst

CAUSAL_CHAIN:
- Memory usage grew past limit
- Kubelet sent SIGKILL

REMEDIATION_STEPS:
- Increase memory limit for payments-api deployment
- Add Datadog monitor for memory usage at 80% threshold
- Review recent traffic patterns for load spikes
"""
        result = parse_root_cause(response)
        assert len(result.remediation_steps) == 3
        assert "payments-api" in result.remediation_steps[0]

    def test_no_remediation_section_returns_empty(self):
        response = """ROOT_CAUSE:
OOM kill detected.

ROOT_CAUSE_CATEGORY:
resource_exhaustion

CAUSAL_CHAIN:
- Memory exceeded limit
"""
        result = parse_root_cause(response)
        assert result.remediation_steps == []

    def test_causal_chain_does_not_swallow_remediation_steps(self):
        response = """ROOT_CAUSE:
Config error.

ROOT_CAUSE_CATEGORY:
configuration_error

CAUSAL_CHAIN:
- Bad env var set
- Service failed to start

REMEDIATION_STEPS:
- Roll back the config change
- Validate env vars in CI
"""
        result = parse_root_cause(response)
        # causal chain must not include remediation lines
        assert not any("Roll back" in step for step in result.causal_chain)
        assert not any("Validate env" in step for step in result.causal_chain)
        assert len(result.causal_chain) == 2
        assert len(result.remediation_steps) == 2

    def test_validated_claims_does_not_swallow_remediation_steps(self):
        """Weak model skips NON_VALIDATED_CLAIMS and CAUSAL_CHAIN — REMEDIATION_STEPS must not leak into validated_claims."""
        response = """ROOT_CAUSE:
OOM kill detected.

ROOT_CAUSE_CATEGORY:
resource_exhaustion

VALIDATED_CLAIMS:
- Pod exited with code 137

REMEDIATION_STEPS:
- Increase memory limit for payments-api
- Add memory alert at 80% threshold
"""
        result = parse_root_cause(response)
        assert not any("Increase memory" in c for c in result.validated_claims)
        assert not any("Add memory alert" in c for c in result.validated_claims)
        assert len(result.remediation_steps) == 2

    def test_non_validated_claims_does_not_swallow_remediation_steps(self):
        """Weak model skips CAUSAL_CHAIN — REMEDIATION_STEPS must still be parsed."""
        response = """ROOT_CAUSE:
OOM kill detected.

ROOT_CAUSE_CATEGORY:
resource_exhaustion

VALIDATED_CLAIMS:
- Pod exited with code 137

NON_VALIDATED_CLAIMS:
- Memory limit may be too low

REMEDIATION_STEPS:
- Increase memory limit for payments-api
- Add memory alert at 80% threshold
"""
        result = parse_root_cause(response)
        assert not any("Increase memory" in c for c in result.non_validated_claims)
        assert not any("Add memory alert" in c for c in result.non_validated_claims)
        assert len(result.remediation_steps) == 2
        assert "payments-api" in result.remediation_steps[0]
