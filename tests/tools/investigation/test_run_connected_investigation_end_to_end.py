"""End-to-end run of the connected investigation pipeline (T-5, #4439).

Runs run_connected_investigation() through the *real* intake, plan_evidence,
gather_evidence, and diagnose stages together (not mocked individually),
with only the LLM and the integration-resolution/delivery boundaries faked —
confirming the pipeline still produces a complete investigation now that all
three LLM-calling stages route through core.agent_harness (build_agent() /
llm_resolution) instead of calling get_llm() directly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from core.domain.alerts.extraction import AlertDetails
from core.llm.types import AgentLLMResponse, ToolCall


class _FakeInvestigationTool:
    def __init__(self, name: str, source: str, output: dict[str, Any]) -> None:
        self.name = name
        self.source = source
        self.public_input_schema: dict[str, Any] = {"properties": {}}
        self._output = output

    def validate_public_input(self, _value: dict[str, Any]) -> str | None:
        return None

    def extract_params(self, _resolved: dict[str, Any]) -> dict[str, Any]:
        return {}

    def run(self, **_kwargs: Any) -> dict[str, Any]:
        return self._output


class _FakeUnifiedLLM:
    """Serves both the structured-output calls (intake, diagnose) and the
    tool-calling loop (gather_evidence) from one fake, in-process client."""

    def __init__(self, structured_result: Any, tool_responses: list[AgentLLMResponse]) -> None:
        self._structured_result = structured_result
        self._tool_responses = list(tool_responses)
        self._tool_invoke_count = 0

    # --- structured-output chain (intake / diagnose) ---
    def with_structured_output(self, _schema: Any) -> _FakeUnifiedLLM:
        return self

    def with_config(self, **_kwargs: Any) -> _FakeUnifiedLLM:
        return self

    # --- tool-calling chain (gather_evidence) ---
    @property
    def model_id(self) -> str | None:
        return "fake-model"

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.input} for tc in tool_calls
            ],
        }

    def build_tool_result_message(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "content": [
                {"id": tc.id, "name": tc.name, "result": result}
                for tc, result in zip(tool_calls, results)
            ],
        }

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        _ = system
        _ = tools
        # Structured-output calls pass a single string prompt, not a message
        # list — distinguish the two chains by argument shape.
        if isinstance(messages, str):
            return self._structured_result
        response = self._tool_responses[self._tool_invoke_count]
        self._tool_invoke_count += 1
        return response


def test_run_connected_investigation_end_to_end_with_fake_llm() -> None:
    from tools.investigation.lifecycle import run_connected_investigation
    from tools.investigation.state_factory import make_initial_state

    fake_tool = _FakeInvestigationTool(
        name="query_generic_logs", source="other", output={"logs": ["boom"]}
    )
    fake_llm = _FakeUnifiedLLM(
        structured_result={
            "root_cause": "The service ran out of memory.",
            "root_cause_category": "Infrastructure",
        },
        tool_responses=[
            AgentLLMResponse(
                content="",
                tool_calls=[ToolCall(id="call_1", name="query_generic_logs", input={})],
                raw_content=None,
            ),
            AgentLLMResponse(content="Final diagnosis text.", tool_calls=[], raw_content=None),
        ],
    )
    fake_alert_details = AlertDetails(
        is_noise=False, alert_name="Pipeline Error", severity="critical"
    )

    state = make_initial_state(raw_alert={"alert_id": "a1", "text": "Something broke."})

    with (
        patch(
            "tools.investigation.stages.resolve_integrations.resolve_integrations",
            return_value={"resolved_integrations": {}},
        ),
        patch(
            "tools.investigation.stages.intake.node.default_reasoning_llm_factory",
            return_value=_FakeUnifiedLLM(fake_alert_details, []),
        ),
        patch(
            "tools.investigation.stages.gather_evidence.agent.get_available_tools",
            return_value=[fake_tool],
        ),
        patch(
            "tools.investigation.stages.gather_evidence.agent.build_seed_calls",
            return_value=[],
        ),
        patch(
            "tools.investigation.stages.gather_evidence.agent.default_llm_factory",
            return_value=fake_llm,
        ),
        patch(
            "tools.investigation.stages.gather_evidence.agent.incident_command_conclusion_complete",
            return_value=True,
        ),
        patch(
            "tools.investigation.stages.diagnose.node.default_reasoning_llm_factory",
            return_value=fake_llm,
        ),
        patch("tools.investigation.reporting.deliver", return_value={}),
    ):
        result = run_connected_investigation(state)

    assert result["is_noise"] is False
    assert result["alert_name"] == "Pipeline Error"
    assert result["evidence"]["query_generic_logs"] == {"logs": ["boom"]}
    assert result["root_cause"] == "The service ran out of memory."
    assert result["root_cause_category"].lower() == "infrastructure"
