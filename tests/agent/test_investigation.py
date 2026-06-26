from __future__ import annotations

import json
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.orchestration.node.investigate import (
    ConnectedInvestigationAgent,
)
from core.orchestration.node.investigate.agent import _tools_for_plan
from core.orchestration.node.investigate.loop import (
    CachedToolResult,
    InvestigationToolCallCache,
    duplicate_call_result,
    tool_call_signature,
)
from core.orchestration.node.investigate.tools import availability_view
from core.runtime import (
    build_synthetic_assistant_tool_call_message,
    context_budget_ceiling_for_model,
    enforce_context_budget,
    estimate_message_tokens,
    execute_tools,
    trim_lowest_value_tool_pair,
)
from integrations.llm_cli.errors import CLITimeoutError
from services.agent_llm_client import CLIBackedAgentClient, ToolCall
from tools.registered_tool import RegisteredTool


def _registered_tool(name: str, source: str) -> RegisteredTool:
    def _run(**_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    return RegisteredTool(
        name=name,
        description=name,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        source=source,  # type: ignore[arg-type]
        run=_run,
    )


def test_tools_for_plan_preserves_plan_order_and_filters_unknown_tools() -> None:
    tools = [
        _registered_tool("query_logs", "datadog"),
        _registered_tool("query_metrics", "datadog"),
        _registered_tool("query_commits", "github"),
    ]

    selected = _tools_for_plan(
        tools,
        {
            "planned_actions": [
                "query_metrics",
                "missing_tool",
                "query_logs",
            ]
        },
    )

    assert [tool.name for tool in selected] == ["query_metrics", "query_logs"]


def test_tools_for_plan_falls_back_when_no_plan_matches() -> None:
    tools = [_registered_tool("query_logs", "datadog")]

    assert _tools_for_plan(tools, {"planned_actions": ["missing_tool"]}) == tools


def test_availability_view_marks_configured_integrations_without_mutating_state() -> None:
    resolved = {"github": {"access_token": "token"}, "_all": [{"service": "github"}]}

    view = availability_view(resolved)

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
    msg = build_synthetic_assistant_tool_call_message(
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
        patch("core.orchestration.node.investigate.agent.get_agent_llm", return_value=mock_llm),
        patch("core.orchestration.node.investigate.agent.get_tracker", return_value=mock_tracker),
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
        patch("core.orchestration.node.investigate.agent.get_agent_llm", return_value=mock_llm),
        patch("core.orchestration.node.investigate.agent.get_tracker", return_value=mock_tracker),
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


def test_run_gracefully_handles_cli_timeout() -> None:
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = CLITimeoutError("antigravity-cli CLI timed out after 300s.")
    mock_llm.tool_schemas.return_value = []

    mock_tracker = MagicMock()

    with (
        patch("core.orchestration.node.investigate.agent.get_agent_llm", return_value=mock_llm),
        patch("core.orchestration.node.investigate.agent.get_tracker", return_value=mock_tracker),
    ):
        agent = ConnectedInvestigationAgent()
        result = agent.run(
            {
                "alert_name": "Test alert",
                "pipeline_name": "test-pipeline",
                "severity": "critical",
                "resolved_integrations": {},
            }
        )

    mock_tracker.error.assert_called_once_with(
        "investigation_agent", message="Failed: LLM timed out"
    )
    assert result["root_cause_category"] == "Investigation Error"
    assert "timed out" in result["root_cause"].lower()
    assert result["remediation_steps"]


def test_run_gracefully_handles_api_timeout_runtime_error() -> None:
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError(
        "Anthropic API failed after 3 attempts: Request timed out."
    )
    mock_llm.tool_schemas.return_value = []

    mock_tracker = MagicMock()

    with (
        patch("core.orchestration.node.investigate.agent.get_agent_llm", return_value=mock_llm),
        patch("core.orchestration.node.investigate.agent.get_tracker", return_value=mock_tracker),
    ):
        agent = ConnectedInvestigationAgent()
        result = agent.run(
            {
                "alert_name": "Test alert",
                "pipeline_name": "test-pipeline",
                "severity": "critical",
                "resolved_integrations": {},
            }
        )

    mock_tracker.error.assert_called_once_with(
        "investigation_agent", message="Failed: LLM timed out"
    )
    assert result["root_cause_category"] == "Investigation Error"
    assert "timed out" in result["root_cause"].lower()


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
        patch("core.orchestration.node.investigate.agent.get_agent_llm", return_value=mock_llm),
        patch("core.orchestration.node.investigate.agent.get_tracker", return_value=mock_tracker),
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
        patch("core.orchestration.node.investigate.agent.get_agent_llm", return_value=mock_llm),
        patch("core.orchestration.node.investigate.agent.get_tracker", return_value=mock_tracker),
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


def test_execute_tools_uses_availability_view_for_classified_integrations() -> None:
    from integrations.config_models import GrafanaIntegrationConfig
    from tools.GrafanaLogsTool import query_grafana_logs

    rt = query_grafana_logs.__opensre_registered_tool__
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.loki_datasource_uid = "loki-uid"
    mock_client.query_loki.return_value = {"success": True, "logs": [], "total_logs": 0}

    resolved = {
        "grafana": GrafanaIntegrationConfig(
            endpoint="https://tracerbio.grafana.net",
            api_key="glsa_test",
        )
    }
    tool_calls = [ToolCall(id="tc1", name="query_grafana_logs", input={"service_name": "checkout"})]

    with patch(
        "tools.GrafanaLogsTool.get_grafana_client_from_credentials",
        return_value=mock_client,
    ) as mock_factory:
        results = execute_tools(tool_calls, [rt], resolved)

    assert results[0]["available"] is True
    mock_factory.assert_called_once_with(
        endpoint="https://tracerbio.grafana.net",
        api_key="glsa_test",
        username="",
        password="",
    )


def testexecute_tools_handles_interpreter_shutdown() -> None:
    """When pool.submit raises RuntimeError (interpreter shutdown), execute_tools
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

    with patch("core.runtime.execution.ThreadPoolExecutor") as mock_executor_cls:
        mock_pool = MagicMock()
        mock_pool.__enter__ = lambda s: s
        mock_pool.__exit__ = MagicMock(return_value=False)
        mock_pool.submit.side_effect = RuntimeError(shutdown_msg)
        mock_executor_cls.return_value = mock_pool

        results = execute_tools(tool_calls, [mock_tool], {})

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

    from services.agent_llm_client import BedrockConverseAgentClient

    llm = BedrockConverseAgentClient(model="mistral.mistral-large-3-675b-instruct")
    calls = [
        ToolCall(id="abc12def3", name="query_logs", input={"query": "error"}),
    ]
    msg = build_synthetic_assistant_tool_call_message(llm, calls)

    assert msg["role"] == "assistant"
    assert msg["content"][0]["toolUse"]["toolUseId"] == "abc12def3"
    assert msg["content"][0]["toolUse"]["name"] == "query_logs"
    assert "I will start by querying" not in str(msg)


def test_estimate_tokens_counts_string_and_block_content() -> None:
    messages = [
        {"role": "user", "content": "x" * 400},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "y" * 200},
                {"type": "tool_use", "id": "t1", "name": "n", "input": {"q": "z" * 100}},
            ],
        },
    ]

    # ~0.25 tokens/char; ceiling-style estimate, exact value not asserted.
    assert estimate_message_tokens(messages) > 100
    assert estimate_message_tokens([]) == 0


def testtrim_lowest_value_tool_pair_drops_assistant_and_following_user_turn() -> None:
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t2", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}],
        },
    ]

    assert trim_lowest_value_tool_pair(messages) is True

    # The first tool_use AND its paired tool_result must be removed together,
    # otherwise Anthropic rejects the conversation.
    assert len(messages) == 3
    assert messages[0]["content"] == "alert"
    assert messages[1]["content"][0]["id"] == "t2"


def testtrim_lowest_value_tool_pair_returns_false_when_no_tool_use_remains() -> None:
    messages = [
        {"role": "user", "content": "alert"},
        {"role": "assistant", "content": [{"type": "text", "text": "plain reply"}]},
    ]

    assert trim_lowest_value_tool_pair(messages) is False
    assert len(messages) == 2


def testtrim_lowest_value_tool_pair_skips_pinned_anthropic_tool_exchange() -> None:
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "seed", "name": "n", "input": {}}],
            "_opensre_seed": True,
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "seed", "content": "seed"}],
            "_opensre_seed": True,
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "later", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "later", "content": "later"}],
        },
    ]

    assert trim_lowest_value_tool_pair(messages) is True

    assert len(messages) == 3
    assert messages[1]["content"][0]["id"] == "seed"
    assert messages[2]["content"][0]["tool_use_id"] == "seed"
    assert all("later" not in json.dumps(message) for message in messages)


def testtrim_lowest_value_tool_pair_returns_false_when_only_pinned_pairs_remain() -> None:
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "seed", "name": "n", "input": {}}],
            "_opensre_seed": True,
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "seed", "content": "seed"}],
            "_opensre_seed": True,
        },
    ]
    snapshot = [message.copy() for message in messages]

    assert trim_lowest_value_tool_pair(messages) is False
    assert messages == snapshot


def testtrim_lowest_value_tool_pair_evicts_duplicate_exchange_before_large_normal_exchange() -> (
    None
):
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "normal", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "normal", "content": "x" * 10_000}],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "dupe", "name": "n", "input": {}}],
            "_opensre_duplicate_result": True,
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "dupe", "content": "duplicate"}],
            "_opensre_duplicate_result": True,
        },
    ]

    assert trim_lowest_value_tool_pair(messages) is True

    assert len(messages) == 3
    assert messages[1]["content"][0]["id"] == "normal"
    assert all("dupe" not in json.dumps(message) for message in messages)


def testtrim_lowest_value_tool_pair_evicts_larger_non_seed_exchange_before_tiny_oldest() -> None:
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tiny", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tiny", "content": "tiny"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "large", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "large", "content": "x" * 10_000}],
        },
    ]

    assert trim_lowest_value_tool_pair(messages) is True

    assert len(messages) == 3
    assert messages[1]["content"][0]["id"] == "tiny"
    assert all("large" not in json.dumps(message) for message in messages)


# --------------------------------------------------------------------------- #
# OpenAI shape — regression pin for the 2026-06-05 floorsweep overflow bug.   #
# Pre-fix, the trim function only recognized Anthropic tool_use blocks inside #
# content lists, so gpt-4o assistant turns (content = plain string,           #
# tool_calls as a top-level field) were never trimmed; long runs hit the 128k #
# context_length_exceeded API error before the ceiling could fire.            #
# --------------------------------------------------------------------------- #


def testtrim_lowest_value_tool_pair_drops_openai_assistant_and_following_tool_messages() -> None:
    """OpenAI shape: assistant has top-level ``tool_calls`` and the results
    arrive as separate ``role: "tool"`` messages with matching call_ids.
    The trimmer must drop the assistant + ALL its matched tool followers."""
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1a", "type": "function", "function": {"name": "n", "arguments": "{}"}},
                {"id": "call_1b", "type": "function", "function": {"name": "n", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1a", "content": "result a"},
        {"role": "tool", "tool_call_id": "call_1b", "content": "result b"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_2", "type": "function", "function": {"name": "n", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_2", "content": "result"},
    ]

    assert trim_lowest_value_tool_pair(messages) is True

    # Drops the OLDEST assistant + both of its tool followers (variable-length
    # exchange, since one assistant turn can issue multiple tool_calls).
    assert len(messages) == 3
    assert messages[0]["content"] == "alert"
    assert messages[1]["tool_calls"][0]["id"] == "call_2"
    assert messages[2]["tool_call_id"] == "call_2"


def testtrim_lowest_value_tool_pair_skips_pinned_openai_tool_exchange() -> None:
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "seed_a", "type": "function", "function": {"name": "n", "arguments": "{}"}},
                {"id": "seed_b", "type": "function", "function": {"name": "n", "arguments": "{}"}},
            ],
            "_opensre_seed": True,
        },
        {"role": "tool", "tool_call_id": "seed_a", "content": "seed a", "_opensre_seed": True},
        {"role": "tool", "tool_call_id": "seed_b", "content": "seed b", "_opensre_seed": True},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "later", "type": "function", "function": {"name": "n", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "later", "content": "later"},
    ]

    assert trim_lowest_value_tool_pair(messages) is True

    assert len(messages) == 4
    assert messages[1]["tool_calls"][0]["id"] == "seed_a"
    assert messages[2]["tool_call_id"] == "seed_a"
    assert messages[3]["tool_call_id"] == "seed_b"
    assert all("later" not in json.dumps(message) for message in messages)


def testtrim_lowest_value_tool_pair_stops_at_unrelated_tool_message_after_openai_assistant() -> (
    None
):
    """Defensive: if a non-matching ``role: "tool"`` message appears after an
    OpenAI assistant turn (shouldn't happen in practice but we don't trust
    upstream message hygiene), we stop walking and drop only the assistant
    and the followers that DO match its call_ids."""
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "n", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        # Stray tool message from a different assistant turn — must not be eaten
        {"role": "tool", "tool_call_id": "orphan", "content": "huh"},
    ]

    assert trim_lowest_value_tool_pair(messages) is True

    # Dropped the assistant + the matching tool, but NOT the orphan
    assert len(messages) == 2
    assert messages[0]["content"] == "alert"
    assert messages[1]["tool_call_id"] == "orphan"


def testtrim_lowest_value_tool_pair_drops_openai_assistant_when_no_tool_messages_follow() -> None:
    """Edge: assistant turn issued tool_calls but the follow-up tool
    messages haven't been appended yet (truncated mid-iteration). Drop just
    the assistant — keeps the conversation valid for the next trim cycle."""
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "n", "arguments": "{}"}},
            ],
        },
    ]

    assert trim_lowest_value_tool_pair(messages) is True
    assert len(messages) == 1
    assert messages[0]["content"] == "alert"


def testtrim_lowest_value_tool_pair_skips_openai_assistant_with_empty_tool_calls() -> None:
    """An assistant message with ``tool_calls: []`` (empty list — e.g. a
    plain reply with no tool requests) must NOT be picked up as trimmable.
    Pin this so a future code path that initializes tool_calls=[] for a
    text-only assistant turn doesn't accidentally get torn out."""
    messages = [
        {"role": "user", "content": "alert"},
        {"role": "assistant", "content": "plain reply", "tool_calls": []},
    ]

    assert trim_lowest_value_tool_pair(messages) is False
    assert len(messages) == 2


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o-2024-11-20", 112_000),  # 128k window − 16k headroom
        ("gpt-5-2025-08-07", 112_000),
        ("gpt-4-turbo", 112_000),
        ("gpt-4.1", 984_000),  # 1M window
        ("claude-3-5-sonnet-20241022", 184_000),  # 200k window
        ("us.anthropic.claude-3-7-sonnet", 184_000),  # Bedrock prefix still matches
        ("some-unknown-model", 112_000),  # conservative default
        (None, 112_000),
        ("", 112_000),
    ],
)
def testcontext_budget_ceiling_for_model(model: str | None, expected: int) -> None:
    """The trim ceiling must track the ACTIVE model's window. A flat ceiling
    overflowed gpt-4o (128k) because it was tuned for Anthropic's 200k — this
    is the regression guard for that bug."""
    assert context_budget_ceiling_for_model(model) == expected


def test_gpt4o_ceiling_is_below_its_hard_limit() -> None:
    """The whole point: gpt-4o's ceiling must leave headroom under 128k so the
    trimmed prompt + response never trips context_length_exceeded."""
    assert context_budget_ceiling_for_model("gpt-4o-2024-11-20") < 128_000


def testenforce_context_budget_respects_explicit_model_ceiling() -> None:
    """A payload that fits a 200k Anthropic ceiling but not a 112k gpt-4o
    ceiling must be trimmed when the gpt-4o ceiling is passed."""
    big = "x" * 300_000  # ~150k tokens at 0.5/char — over 112k, under 184k
    messages: list[dict] = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "k", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": big}],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t2", "name": "k", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "small"}],
        },
    ]
    enforce_context_budget(messages, ceiling=context_budget_ceiling_for_model("gpt-4o"))
    # Oldest big pair trimmed; the small t2 pair survives.
    assert len(messages) == 3
    assert all("t1" not in json.dumps(m) for m in messages)


def testenforce_context_budget_noop_when_under_ceiling() -> None:
    messages: list[dict] = [
        {"role": "user", "content": "short alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
    ]
    snapshot = [m.copy() for m in messages]

    enforce_context_budget(messages)

    assert messages == snapshot


# --------------------------------------------------------------------------- #
# Termination hook — production default + override mechanics                  #
# --------------------------------------------------------------------------- #


def test_should_accept_conclusion_production_default_accepts() -> None:
    """Production default: the agent ALWAYS accepts the LLM's choice to stop.
    Returning (True, None) is the no-op path; subclasses override to enforce
    floors or other termination policies."""
    agent = ConnectedInvestigationAgent()
    accept, nudge = agent._should_accept_conclusion(evidence_count=0, iteration=0)
    assert accept is True
    assert nudge is None
    # Behavior independent of how many tool calls happened — production
    # agents trust the LLM.
    accept, nudge = agent._should_accept_conclusion(evidence_count=100, iteration=15)
    assert accept is True
    assert nudge is None


def test_invalid_hook_return_false_none_raises_at_call_site() -> None:
    """Greptile P1: a hook override that returns ``(False, None)`` would
    spin the loop on an unchanged message history until
    ``MAX_INVESTIGATION_LOOPS``, silently burning the whole token budget.
    The call site must raise immediately so buggy overrides fail loud
    instead of expensive.

    This pins the contract — a future regression that drops the guard
    fails here instead of in a production token-burn incident."""

    class _BadAgent(ConnectedInvestigationAgent):
        def _should_accept_conclusion(
            self,
            *,
            evidence_count: int,  # noqa: ARG002 — base signature
            iteration: int,  # noqa: ARG002 — base signature
        ) -> tuple[bool, str | None]:
            return False, None  # invalid — rejects without providing context

    mock_llm = MagicMock()
    # Empty content + no tool calls → LLM "concludes" → triggers the hook.
    mock_response = MagicMock()
    mock_response.has_tool_calls = False
    mock_response.tool_calls = []
    mock_response.content = ""
    mock_response.raw_content = None
    mock_llm.invoke.return_value = mock_response
    mock_llm.tool_schemas.return_value = []
    mock_tracker = MagicMock()

    state = {
        "alert_name": "Test alert",
        "pipeline_name": "test-pipeline",
        "severity": "critical",
        "resolved_integrations": {},
    }
    agent = _BadAgent()
    with (
        patch("core.orchestration.node.investigate.agent.get_agent_llm", return_value=mock_llm),
        patch("core.orchestration.node.investigate.agent.get_tracker", return_value=mock_tracker),
        pytest.raises(ValueError, match="_should_accept_conclusion returned"),
    ):
        agent.run(state)


def test_should_accept_conclusion_subclass_can_force_continuation() -> None:
    """Subclasses can return (False, nudge) to keep the loop going.
    This is what BenchInvestigationAgent does to enforce minimum evidence."""

    class _StrictAgent(ConnectedInvestigationAgent):
        def _should_accept_conclusion(
            self,
            *,
            evidence_count: int,
            iteration: int,  # noqa: ARG002 — base signature
        ) -> tuple[bool, str | None]:
            if evidence_count >= 5:
                return True, None
            return False, f"Only {evidence_count} tool calls so far — keep going."

    agent = _StrictAgent()
    accept, nudge = agent._should_accept_conclusion(evidence_count=3, iteration=2)
    assert accept is False
    assert nudge is not None and "3 tool calls" in nudge

    accept, nudge = agent._should_accept_conclusion(evidence_count=7, iteration=5)
    assert accept is True
    assert nudge is None


def testenforce_context_budget_trims_when_over_ceiling() -> None:
    # Each tool turn carries ~1 MB of text (~250k token estimate). One pair
    # is enough to push messages past the 180k ceiling; the function should
    # trim it.
    big_payload = "x" * 1_000_000
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": big_payload}],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t2", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}],
        },
    ]

    enforce_context_budget(messages)

    # Oldest pair (t1 with the big payload) must be gone; the t2 pair survives.
    assert len(messages) == 3
    assert messages[1]["content"][0]["id"] == "t2"


def testenforce_context_budget_preserves_pinned_seed_pair_before_truncation() -> None:
    ceiling = 50_000
    big_seed_payload = "s" * 200_000
    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "seed", "name": "n", "input": {}}],
            "_opensre_seed": True,
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "seed", "content": big_seed_payload}
            ],
            "_opensre_seed": True,
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "later", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "later", "content": "later"}],
        },
    ]

    enforce_context_budget(messages, ceiling=ceiling)

    assert len(messages) == 3
    assert messages[1]["content"][0]["id"] == "seed"
    assert messages[2]["content"][0]["tool_use_id"] == "seed"
    assert messages[2]["content"][0]["content"].endswith(_MARKER)
    assert all("later" not in json.dumps(message) for message in messages)
    assert estimate_message_tokens(messages) <= ceiling


# --------------------------------------------------------------------------- #
# Last-resort truncation. Whole-pair trimming drops low-value tool exchanges    #
# but cannot shrink the base prompt (e.g. an oversized initial alert / non-tool #
# message). The old code returned there and overflowed the API; these pin the   #
# truncation fallback that closes that crash vector.                            #
# --------------------------------------------------------------------------- #

_MARKER = "…[truncated to fit context budget]"


def testenforce_context_budget_truncates_oversized_string_base_prompt() -> None:
    """A huge initial user message (string content) with no trimmable tool pair
    must be truncated, not left to overflow."""
    ceiling = 50_000
    big = "x" * 1_000_000  # ~500k token estimate at 0.5 tokens/char — alone over ceiling
    messages = [{"role": "user", "content": big}]

    enforce_context_budget(messages, ceiling=ceiling)

    assert estimate_message_tokens(messages) <= ceiling
    assert len(messages[0]["content"]) < len(big)
    assert messages[0]["content"].endswith(_MARKER)


def testenforce_context_budget_truncates_oversized_list_content_base_prompt() -> None:
    """A user message whose list content (Anthropic text blocks) is over budget
    and isn't part of a tool pair must be truncated in place, structure intact."""
    ceiling = 50_000
    big = "y" * 1_000_000
    messages = [{"role": "user", "content": [{"type": "text", "text": big}]}]

    enforce_context_budget(messages, ceiling=ceiling)

    assert estimate_message_tokens(messages) <= ceiling
    block = messages[0]["content"][0]
    assert block["type"] == "text"  # structure preserved
    assert len(block["text"]) < len(big)
    assert block["text"].endswith(_MARKER)


def testenforce_context_budget_trims_pairs_then_truncates_base_prompt() -> None:
    """Mixed: a trimmable tool pair AND an oversized base alert. The trimmer drops
    the pair first; truncation then shrinks the remaining oversized alert."""
    ceiling = 50_000
    big = "z" * 1_000_000
    messages = [
        {"role": "user", "content": big},  # oversized base alert (not a tool pair)
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "n", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "small"}],
        },
    ]

    enforce_context_budget(messages, ceiling=ceiling)

    assert estimate_message_tokens(messages) <= ceiling
    # The t1 tool pair was trimmed away entirely.
    assert all(
        not (
            isinstance(m.get("content"), list)
            and m["content"]
            and isinstance(m["content"][0], dict)
            and m["content"][0].get("type") == "tool_use"
        )
        for m in messages
    )
    # The remaining oversized alert was truncated.
    assert messages[0]["role"] == "user"
    assert len(messages[0]["content"]) < len(big)
    assert messages[0]["content"].endswith(_MARKER)


def testenforce_context_budget_returns_when_only_untruncatable_overhead() -> None:
    """If system+tools alone exceed the ceiling and messages have no shrinkable
    text, the function must return (no infinite loop) and let the API surface it.
    """
    ceiling = 10_000
    # A huge tool schema pushes overhead past the ceiling; the single message has
    # only a tiny, already-minimal payload that truncation can't usefully shrink.
    tools = [{"name": "big", "schema": "s" * 1_000_000}]
    messages = [{"role": "user", "content": "tiny"}]

    # Must terminate quickly rather than spin.
    enforce_context_budget(messages, tools=tools, ceiling=ceiling)

    assert messages == [{"role": "user", "content": "tiny"}]


# --------------------------------------------------------------------------- #
# Duplicate-call guard + stagnation breaker. The 2026-06-18 report showed a    #
# generic alert spinning to MAX_INVESTIGATION_LOOPS while re-running           #
# list_posthog_tools x15 / get_sre_guidance x14 — identical calls that return  #
# no new evidence. Context trimming erases the history that would remind the   #
# model it already ran them, so the dedup ledger is tracked in Python instead. #
# --------------------------------------------------------------------------- #


def test_tool_call_signature_is_argument_order_independent() -> None:
    a = ToolCall(id="1", name="query", input={"service": "x", "window": "1h"})
    b = ToolCall(id="2", name="query", input={"window": "1h", "service": "x"})
    c = ToolCall(id="3", name="query", input={"service": "y", "window": "1h"})

    assert tool_call_signature(a) == tool_call_signature(b)
    assert tool_call_signature(a) != tool_call_signature(c)


def test_duplicate_call_result_marks_suppression() -> None:
    cached = CachedToolResult(result={"logs": ["error A"]}, loop_iteration=2)
    result = duplicate_call_result(ToolCall(id="1", name="list_posthog_tools", input={}), cached)

    assert result["suppressed_duplicate"] is True
    assert result["reused_cached_result"] is True
    assert result["tool"] == "list_posthog_tools"
    assert result["cached_result"] == {"logs": ["error A"]}
    assert "lap 3" in result["note"]


def test_investigation_tool_call_cache_lookup_after_store() -> None:
    cache = InvestigationToolCallCache()
    signature = tool_call_signature(ToolCall(id="1", name="query_logs", input={"svc": "api"}))

    assert cache.lookup(signature) is None
    cache.store(signature, {"lines": 3}, loop_iteration=0)

    cached = cache.lookup(signature)
    assert cached is not None
    assert cached.result == {"lines": 3}
    assert cached.loop_iteration == 0


def test_investigation_tool_call_cache_first_write_wins() -> None:
    cache = InvestigationToolCallCache()
    signature = tool_call_signature(ToolCall(id="1", name="query_logs", input={"svc": "api"}))

    cache.store(signature, {"lines": 3}, loop_iteration=0)
    cache.store(signature, {"lines": 99}, loop_iteration=1)

    cached = cache.lookup(signature)
    assert cached is not None
    assert cached.result == {"lines": 3}
    assert cached.loop_iteration == 0


def test_duplicate_call_result_truncates_large_cached_payload() -> None:
    cached = CachedToolResult(result={"logs": "x" * 20_000}, loop_iteration=0)
    result = duplicate_call_result(ToolCall(id="1", name="query_logs", input={}), cached)

    payload = result["cached_result"]
    assert isinstance(payload, dict)
    assert payload["_truncated_for_duplicate_replay"] is True
    assert len(payload["preview"]) <= 8_000


def _fake_tool(name: str, *, source: str = "posthog_mcp") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.source = source
    tool.validate_public_input.return_value = None
    tool.extract_params.return_value = {}
    tool.run.return_value = {"ok": True, "tool": name}
    return tool


def _tool_call_response(tool_calls: list[ToolCall]) -> MagicMock:
    response = MagicMock()
    response.tool_calls = tool_calls
    response.has_tool_calls = True
    response.content = ""
    response.raw_content = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
            }
            for tc in tool_calls
        ],
    }
    return response


def _text_response(text: str) -> MagicMock:
    response = MagicMock()
    response.tool_calls = []
    response.has_tool_calls = False
    response.content = text
    response.raw_content = {"role": "assistant", "content": text}
    return response


def _run_agent_with_scripted_llm(
    *,
    invoke: Any,
    tools: list[MagicMock],
) -> tuple[dict[str, Any], MagicMock]:
    mock_llm = MagicMock()
    mock_llm._model = "gpt-4o"
    mock_llm.tool_schemas.return_value = [{"name": t.name} for t in tools]
    mock_llm.invoke.side_effect = invoke
    mock_llm.build_tool_result_message.side_effect = lambda _calls, results: {
        "role": "user",
        "content": json.dumps(results, default=str),
    }

    state = {
        "alert_name": "Test alert",
        "pipeline_name": "test-pipeline",
        "severity": "critical",
        "resolved_integrations": {},
    }

    with (
        patch("core.orchestration.node.investigate.agent.get_agent_llm", return_value=mock_llm),
        patch("core.orchestration.node.investigate.agent.get_tracker", return_value=MagicMock()),
        patch("core.orchestration.node.investigate.agent.get_available_tools", return_value=tools),
    ):
        result = ConnectedInvestigationAgent().run(state)
    return result, mock_llm


def test_run_suppresses_duplicate_tool_calls() -> None:
    """A tool re-requested with identical arguments is NOT executed again."""
    tool = _fake_tool("list_posthog_tools")
    responses = [
        _tool_call_response([ToolCall(id="c1", name="list_posthog_tools", input={})]),
        # identical call — must be suppressed, not re-run
        _tool_call_response([ToolCall(id="c2", name="list_posthog_tools", input={})]),
        _text_response("Final diagnosis."),
    ]

    result, mock_llm = _run_agent_with_scripted_llm(invoke=responses, tools=[tool])

    # Executed exactly once despite being requested twice.
    assert tool.run.call_count == 1
    # The duplicate got the wrapped cached result fed back to the model.
    assert any(
        isinstance(m.get("content"), str)
        and "suppressed_duplicate" in m["content"]
        and "cached_result" in m["content"]
        and '"ok": true' in m["content"].lower()
        for m in result["agent_messages"]
    )
    duplicate_messages = [
        m for m in result["agent_messages"] if m.get("_opensre_duplicate_result") is True
    ]
    assert len(duplicate_messages) == 2
    assert duplicate_messages[0]["role"] == "assistant"
    assert "suppressed_duplicate" in duplicate_messages[1]["content"]
    assert mock_llm.invoke.call_count == 3


def test_run_does_not_suppress_calls_with_different_args() -> None:
    """Same tool, different arguments is legitimate and must still execute."""
    tool = _fake_tool("query_logs")
    responses = [
        _tool_call_response([ToolCall(id="c1", name="query_logs", input={"svc": "a"})]),
        _tool_call_response([ToolCall(id="c2", name="query_logs", input={"svc": "b"})]),
        _text_response("Final diagnosis."),
    ]

    result = _run_agent_with_scripted_llm(invoke=responses, tools=[tool])[0]
    assert tool.run.call_count == 2
    assert result["agent_messages"][-1]["content"] == "Final diagnosis."


def test_run_forces_conclusion_when_stuck_repeating() -> None:
    """A model that loops on the same call is forced to conclude well before
    MAX_INVESTIGATION_LOOPS=20. When the runtime offers no tools (the forced
    conclusion turn), the model must produce its diagnosis."""
    tool = _fake_tool("get_sre_guidance", source="knowledge")

    def invoke(messages: Any, system: Any, tools: Any) -> MagicMock:  # noqa: ARG001
        # No tools offered → forced conclusion turn → return text.
        if not tools:
            return _text_response("Final diagnosis: insufficient evidence.")
        # Stubborn model: always re-requests the same call.
        return _tool_call_response([ToolCall(id="c", name="get_sre_guidance", input={})])

    result, mock_llm = _run_agent_with_scripted_llm(invoke=invoke, tools=[tool])

    # Ran the real tool exactly once (first, fresh); every repeat was suppressed.
    assert tool.run.call_count == 1
    # Converged far below the 20-iteration cap instead of spinning.
    assert mock_llm.invoke.call_count < 6
    # The final forced turn was invoked with NO tools.
    assert mock_llm.invoke.call_args_list[-1].kwargs["tools"] == []
    assert result["agent_messages"][-1]["content"] == "Final diagnosis: insufficient evidence."


def test_truncate_content_distributes_across_multiple_blocks() -> None:
    """List content with several text slots is shrunk proportionally so the whole
    message lands near the budget instead of zeroing the first slot only."""
    from core.runtime import truncate_content

    content = [
        {"type": "text", "text": "a" * 100_000},
        {"type": "tool_result", "tool_use_id": "t", "content": "b" * 100_000},
    ]

    new_content, changed = truncate_content(content, max_chars=10_000)

    assert changed is True
    total = len(new_content[0]["text"]) + len(new_content[1]["content"])
    # Both slots contributed to the reduction (proportional, not all-from-one).
    assert len(new_content[0]["text"]) < 100_000
    assert len(new_content[1]["content"]) < 100_000
    assert total <= 10_000 + 2 * len("…[truncated to fit context budget]")
