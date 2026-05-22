from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from app.agent.investigation import (
    ConnectedInvestigationAgent,
    _availability_view,
    _build_synthetic_assistant_tool_call_msg,
    _run_parallel,
)
from app.services.agent_llm_client import CLIBackedAgentClient, ToolCall


def test_availability_view_marks_configured_integrations_without_mutating_state() -> None:
    resolved = {"github": {"access_token": "token"}, "_all": [{"service": "github"}]}

    view = _availability_view(resolved)

    assert view["github"]["connection_verified"] is True
    assert "connection_verified" not in resolved["github"]
    assert view["_all"] == resolved["_all"]


def test_build_synthetic_assistant_json_for_cli_backed_client() -> None:
    """Seed assistant turn must match CLI JSON history format (Greptile)."""
    import types as _types

    fake_adapter = _types.SimpleNamespace(
        name="codex",
        binary_env_key="CODEX_BIN",
        install_hint="",
        auth_hint="codex login",
        default_exec_timeout_sec=30.0,
        detect=lambda: _types.SimpleNamespace(
            installed=True, bin_path="/x", logged_in=True, detail=""
        ),
        build=lambda **_kw: _types.SimpleNamespace(
            argv=("/x",), stdin="", cwd="/", env=None, timeout_sec=30.0
        ),
        parse=lambda **_kw: "",
        explain_failure=lambda **_kw: "",
    )
    llm = CLIBackedAgentClient(fake_adapter, model=None)
    msg = _build_synthetic_assistant_tool_call_msg(
        llm,
        [ToolCall(id="seed_t", name="query_eks", input={"cluster": "c"})],
    )
    assert msg["role"] == "assistant"
    assert '"tool_calls"' in msg["content"]
    assert "query_eks" in msg["content"]
    assert "seed_t" in msg["content"]


def test_run_gracefully_handles_model_not_found_runtime_error() -> None:
    """When the LLM raises a model-not-found RuntimeError, the agent should
    return a degraded state dict instead of crashing the pipeline."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError("OpenAI model 'qwen' not found.")
    mock_llm.tool_schemas.return_value = []

    mock_tracker = MagicMock()

    with (
        patch("app.agent.investigation.get_agent_llm", return_value=mock_llm),
        patch("app.agent.investigation.get_tracker", return_value=mock_tracker),
    ):
        agent = ConnectedInvestigationAgent()
        state = {
            "alert_name": "Test alert",
            "pipeline_name": "test-pipeline",
            "severity": "critical",
            "resolved_integrations": {},
        }
        result = agent.run(state)

    mock_tracker.error.assert_called_once_with(
        "investigation_agent", message="Failed: Model not found"
    )
    assert result["root_cause_category"] == "Configuration Error"
    assert result["validity_score"] == 0.0
    assert "not found" in result["root_cause"].lower()
    assert result["remediation_steps"]
    assert result["causal_chain"]


def test_run_re_raises_unmatched_runtime_error() -> None:
    """RuntimeError messages that do not match the model-not-found heuristic
    should be re-raised so upstream handlers can deal with them."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError("Some other API failure")
    mock_llm.tool_schemas.return_value = []

    mock_tracker = MagicMock()

    with (
        patch("app.agent.investigation.get_agent_llm", return_value=mock_llm),
        patch("app.agent.investigation.get_tracker", return_value=mock_tracker),
    ):
        agent = ConnectedInvestigationAgent()
        state = {
            "alert_name": "Test alert",
            "pipeline_name": "test-pipeline",
            "severity": "critical",
            "resolved_integrations": {},
        }
        with pytest.raises(RuntimeError, match="Some other API failure"):
            agent.run(state)

    mock_tracker.error.assert_not_called()


@pytest.mark.parametrize(
    "error_msg",
    [
        "OpenAI request rejected: Error code: 400 - {'error': {'message': 'registry.ollama.ai/library/llama3:latest does not support tools'}}",
        "OpenAI request rejected: Error code: 400 - {'error': {'message': 'llama3:latest does not support tool calls'}}",
    ],
)
def test_run_gracefully_handles_tool_unsupported_model(error_msg: str) -> None:
    """When the LLM raises a 'does not support tools' error the agent returns
    a degraded state with a clear configuration-error message."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError(error_msg)
    mock_llm.tool_schemas.return_value = []

    mock_tracker = MagicMock()

    with (
        patch("app.agent.investigation.get_agent_llm", return_value=mock_llm),
        patch("app.agent.investigation.get_tracker", return_value=mock_tracker),
    ):
        agent = ConnectedInvestigationAgent()
        state = {
            "alert_name": "Test alert",
            "pipeline_name": "test-pipeline",
            "severity": "critical",
            "resolved_integrations": {},
        }
        result = agent.run(state)

    mock_tracker.error.assert_called_once_with(
        "investigation_agent", message="Failed: Model does not support tools"
    )
    assert result["root_cause_category"] == "Configuration Error"
    assert result["validity_score"] == 0.0
    assert "tool calling" in result["root_cause"].lower()
    assert result["remediation_steps"]
    assert result["causal_chain"]


def test_run_gracefully_handles_single_tool_call_only_model() -> None:
    """When the provider reports that a model only supports single tool-calls
    the agent returns a degraded state with a clear configuration-error message."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError(
        "OpenAI API failed: Error code: 500 - {'error': {'message': "
        "'This model only supports single tool-calls at once! (in tool_use:95)'}}"
    )
    mock_llm.tool_schemas.return_value = []

    mock_tracker = MagicMock()

    with (
        patch("app.agent.investigation.get_agent_llm", return_value=mock_llm),
        patch("app.agent.investigation.get_tracker", return_value=mock_tracker),
    ):
        agent = ConnectedInvestigationAgent()
        state = {
            "alert_name": "Test alert",
            "pipeline_name": "test-pipeline",
            "severity": "critical",
            "resolved_integrations": {},
        }
        result = agent.run(state)

    mock_tracker.error.assert_called_once_with(
        "investigation_agent", message="Failed: Model does not support tools"
    )
    assert result["root_cause_category"] == "Configuration Error"
    assert result["validity_score"] == 0.0
    assert "tool calling" in result["root_cause"].lower()
    assert result["remediation_steps"]
    assert result["causal_chain"]


def test_run_parallel_handles_interpreter_shutdown() -> None:
    """When pool.submit raises RuntimeError (interpreter shutdown), _run_parallel
    must fall back to sequential execution and still return results for all slots."""
    mock_tool = MagicMock()
    mock_tool.name = "good_tool"
    mock_tool.validate_public_input.return_value = None
    mock_tool.extract_params.return_value = {}
    mock_tool.run.return_value = {"result": "ok"}

    tool_calls = [
        ToolCall(id="tc1", name="good_tool", input={}),
        ToolCall(id="tc2", name="good_tool", input={}),
    ]

    shutdown_msg = "cannot schedule new futures after interpreter shutdown"

    with patch("app.agent.investigation.ThreadPoolExecutor") as mock_executor_cls:
        mock_pool = MagicMock()
        mock_pool.__enter__ = lambda s: s
        mock_pool.__exit__ = MagicMock(return_value=False)
        mock_pool.submit.side_effect = RuntimeError(shutdown_msg)
        mock_executor_cls.return_value = mock_pool

        results = _run_parallel(tool_calls, [mock_tool], {})

    # The concurrent path raises RuntimeError; fallback sequential execution succeeds
    assert len(results) == 2
    assert all(r == {"result": "ok"} for r in results)


def test_build_synthetic_assistant_msg_for_bedrock_converse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed assistant turn must use Converse toolUse blocks, not plain text fallback."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(
            client=lambda *_args, **_kwargs: types.SimpleNamespace(converse=lambda **_: {})
        ),
    )

    from app.services.agent_llm_client import BedrockConverseAgentClient

    llm = BedrockConverseAgentClient(model="mistral.mistral-large-3-675b-instruct")
    calls = [
        ToolCall(id="abc12def3", name="query_logs", input={"query": "error"}),
    ]
    msg = _build_synthetic_assistant_tool_call_msg(llm, calls)

    assert msg["role"] == "assistant"
    assert msg["content"][0]["toolUse"]["toolUseId"] == "abc12def3"
    assert msg["content"][0]["toolUse"]["name"] == "query_logs"
    assert "I will start by querying" not in str(msg)
