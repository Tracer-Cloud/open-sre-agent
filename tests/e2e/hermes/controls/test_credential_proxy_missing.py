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
def test_credential_proxy_missing() -> None:
    state = run_hermes_scenario("044-credential-proxy-missing")

    category = str(state.get("root_cause_category", "")).lower()

    assert category in {
        "missing_credential_isolation",
        "configuration_error",
        "code_defect",
    }

    assert float(state.get("validity_score") or 0.0) > 0.7

    response = str(state.get("root_cause_summary") or state.get("analysis") or state).lower()

    assert "credential" in response

    assert "proxy" in response or "in-process" in response or "isolation" in response
