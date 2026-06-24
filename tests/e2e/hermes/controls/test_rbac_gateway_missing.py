from __future__ import annotations

import pytest

from tests.e2e.hermes.common import (
    LLM_CREDENTIAL_SKIP_REASON,
    llm_ready,
)
from tests.e2e.hermes.orchestrator import run_hermes_scenario

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(
    not llm_ready(),
    reason=LLM_CREDENTIAL_SKIP_REASON,
)
def test_rbac_gateway_missing() -> None:
    state = run_hermes_scenario("043-rbac-gateway-missing-multi-user")

    category = str(state.get("root_cause_category", "")).lower()

    assert category in {
        "missing_rbac",
        "configuration_error",
        "code_defect",
    }

    assert float(state.get("validity_score") or 0.0) > 0.7

    response = str(state.get("root_cause_summary") or state.get("analysis") or state).lower()

    assert "rbac" in response or "authorization" in response

    assert "cross-tenant" in response or "scope" in response or "tenant isolation" in response
