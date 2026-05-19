from __future__ import annotations

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


def _make_tool_call(name: str, id_: str = "call-1") -> ToolCall:
    return ToolCall(id=id_, name=name, input={})


def _registered_tool(name: str, run_value: object) -> MagicMock:
    """Build a minimal stand-in for ``RegisteredTool`` that ``_run_parallel`` can drive."""
    tool = MagicMock(name=f"tool[{name}]")
    tool.name = name
    tool.extract_params.return_value = {}
    tool.run.return_value = run_value
    return tool


def test_run_parallel_falls_back_to_sequential_on_interpreter_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sentry-reported #2194: ``ThreadPoolExecutor.submit`` can race the interpreter
    shutdown handlers and raise ``RuntimeError: cannot schedule new futures after
    interpreter shutdown``. _run_parallel must catch that, drop back to sequential
    execution, and surface real tool results instead of letting the investigation
    lose every parallel tool call."""

    def _boom_submit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("cannot schedule new futures after interpreter shutdown")

    monkeypatch.setattr(
        "app.agent.investigation.ThreadPoolExecutor.submit",
        _boom_submit,
    )

    tool_calls = [_make_tool_call("alpha", id_="a"), _make_tool_call("beta", id_="b")]
    tools = [
        _registered_tool("alpha", {"out": "A"}),
        _registered_tool("beta", {"out": "B"}),
    ]

    results = _run_parallel(tool_calls, tools, resolved_integrations={})

    assert results == [{"out": "A"}, {"out": "B"}]


def test_run_parallel_skips_executor_when_interpreter_is_finalizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``sys.is_finalizing()`` is already true when _run_parallel is entered,
    we should never touch the executor — go straight to sequential execution."""
    monkeypatch.setattr("app.agent.investigation.sys.is_finalizing", lambda: True)

    submit_calls: list[object] = []

    def _track_submit(*args: object, **kwargs: object) -> None:
        submit_calls.append((args, kwargs))
        raise AssertionError("submit must not be called when sys.is_finalizing()")

    monkeypatch.setattr(
        "app.agent.investigation.ThreadPoolExecutor.submit",
        _track_submit,
    )

    tool_calls = [_make_tool_call("alpha", id_="a"), _make_tool_call("beta", id_="b")]
    tools = [
        _registered_tool("alpha", "out-A"),
        _registered_tool("beta", "out-B"),
    ]

    results = _run_parallel(tool_calls, tools, resolved_integrations={})

    assert results == ["out-A", "out-B"]
    assert submit_calls == []


def test_run_parallel_re_raises_non_shutdown_runtime_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: a generic RuntimeError that's *not* the interpreter-shutdown
    race must still propagate so we don't accidentally swallow real bugs in
    the executor wiring."""

    def _boom_submit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("queue is full")  # unrelated to shutdown

    monkeypatch.setattr(
        "app.agent.investigation.ThreadPoolExecutor.submit",
        _boom_submit,
    )

    tool_calls = [_make_tool_call("alpha", id_="a"), _make_tool_call("beta", id_="b")]
    tools = [_registered_tool("alpha", "x"), _registered_tool("beta", "y")]

    with pytest.raises(RuntimeError, match="queue is full"):
        _run_parallel(tool_calls, tools, resolved_integrations={})
