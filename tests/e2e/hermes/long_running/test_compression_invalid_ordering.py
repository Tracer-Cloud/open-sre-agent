from __future__ import annotations

import pytest

from tests.e2e.hermes.long_running.conftest import LLM_CREDENTIAL_SKIP_REASON, llm_ready
from tests.e2e.hermes.orchestrator import run_hermes_scenario

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(not llm_ready(), reason=LLM_CREDENTIAL_SKIP_REASON)
def test_compression_invalid_ordering() -> None:
    state = run_hermes_scenario("010-compression-invalid-tool-ordering")
    assert str(state.get("root_cause_category", "")).lower() == "agent_state_corruption"
    assert float(state.get("validity_score") or 0.0) > 0.7
