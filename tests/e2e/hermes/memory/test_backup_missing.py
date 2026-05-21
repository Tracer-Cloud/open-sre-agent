from __future__ import annotations

import pytest

from tests.e2e.hermes.common import LLM_CREDENTIAL_SKIP_REASON, llm_ready
from tests.e2e.hermes.orchestrator import run_hermes_scenario

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(not llm_ready(), reason=LLM_CREDENTIAL_SKIP_REASON)
def test_memory_backup_missing() -> None:
    state = run_hermes_scenario("031-memory-backup-missing")

    assert str(state.get("root_cause_category", "")).lower() in {
        "memory_corruption",
        "configuration_error",
    }
    assert float(state.get("validity_score") or 0.0) > 0.7
