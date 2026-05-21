from __future__ import annotations

import pytest

from tests.e2e.hermes.common import LLM_CREDENTIAL_SKIP_REASON, llm_ready
from tests.e2e.hermes.orchestrator import run_hermes_scenario

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(not llm_ready(), reason=LLM_CREDENTIAL_SKIP_REASON)
def test_memory_tool_json_parse_llama_cpp() -> None:
    state = run_hermes_scenario("032-memory-tool-json-parse-llama-cpp")

    assert str(state.get("root_cause_category", "")).lower() in {
        "memory_parse_failure",
        "configuration_error",
        "code_defect_serialization",
    }
    assert float(state.get("validity_score") or 0.0) > 0.7
