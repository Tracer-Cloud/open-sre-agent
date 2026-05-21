from __future__ import annotations

import pytest

from tests.e2e.hermes.common import LLM_CREDENTIAL_SKIP_REASON, llm_ready
from tests.e2e.hermes.orchestrator import run_hermes_scenario

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(not llm_ready(), reason=LLM_CREDENTIAL_SKIP_REASON)
def test_multi_model_routing_ignored() -> None:
    state = run_hermes_scenario("022-multi-model-routing-ignored")

    assert str(state.get("root_cause_category", "")).lower() in {
        "routing_ignored",
        "configuration_error",
        "env_var_misconfiguration",
    }

    assert float(state.get("validity_score") or 0.0) > 0.7
