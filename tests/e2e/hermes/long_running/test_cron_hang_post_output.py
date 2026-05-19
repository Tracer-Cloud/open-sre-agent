from __future__ import annotations

import pytest

from tests.e2e.hermes.long_running.conftest import LLM_CREDENTIAL_SKIP_REASON, llm_ready
from tests.e2e.hermes.orchestrator import run_hermes_scenario

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(not llm_ready(), reason=LLM_CREDENTIAL_SKIP_REASON)
def test_cron_hang_post_output() -> None:
    state = run_hermes_scenario("012-cron-hang-post-output")
    assert str(state.get("root_cause_category", "")).lower() == "delivery_hang"
    assert float(state.get("validity_score") or 0.0) > 0.7
