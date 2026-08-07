"""Action-execution tests over model tool calls, not planner DTOs."""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

import pytest
from rich.console import Console

import surfaces.interactive_shell.runtime.slash_adapter as slash_adapter
from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
from core.agent_harness.ports import AnswerRequest
from core.agent_harness.prompts.memory.prior_investigation import (
    PRIOR_INVESTIGATION_RECALL_SECONDS,
)
from core.agent_harness.session.pending_offer import first_pending_offer
from core.agent_harness.turns.action_driver import (
    ActionTurnPlan,
    ActionTurnRunner,
    ToolCallingDeps,
    _build_action_agent,
    _turn_resolved_integrations,
)
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool_framework.registered_tool import RegisteredTool
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


class _GenericActionToolProvider:
    def __init__(self, tool: RegisteredTool) -> None:
        self._tool = tool

    def action_tools(self, **_kwargs: object) -> list[RegisteredTool]:
        return [self._tool]

    def observer(self, **_kwargs: object):
        return lambda _kind, _data: None


class _OutputSink:
    def __init__(self, console: Console) -> None:
        self._console = console

    def print(self, message: str = "") -> None:
        self._console.print(message)

    def render_response_header(self, label: str) -> None:
        self._console.print(label)

    def render_error(self, message: str) -> None:
        self._console.print(message)

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
        text = "".join(chunks)
        self._console.print(text)
        return text


def test_execute_with_harness_runs_slash_tool_call(monkeypatch) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)
    harness = ActionExecutionHarness(
        llm=FakeActionLLM([tool_response("slash_invoke", {"command": "/health", "args": []})])
    )
    session = Session()

    result = run_action_tool_turn(
        "check health",
        session,
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 1
    assert dispatched == ["/health"]
    assert "slash_invoke" in harness.llm.tool_schema_names


def test_slash_invoke_suppresses_hallucinated_success_closing(monkeypatch) -> None:
    """After /health (self-recording), the model must not invent "everything's green".

    slash_invoke already printed the real report; a closing line that claims
    success without reading that output is shown under ● assistant and misleads.
    """

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        session.record("slash", command, ok=True)
        console.print("Summary: 3 passed  |  4 failed")
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)
    lied = "Health check passed — everything's green. ✅"
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response("slash_invoke", {"command": "/health", "args": []}),
                no_tool_response(lied),
            ]
        )
    )

    result = run_action_tool_turn(
        "yes do that health check",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    console_text = harness.console_buffer.getvalue()
    assert "3 passed" in console_text
    assert lied not in console_text
    assert "everything's green" not in result.response_text


def test_generic_registered_action_tool_result_marks_turn_handled() -> None:
    tool = RegisteredTool(
        name="fake_send_message",
        description="Send a fake message.",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        source="knowledge",
        surfaces=("action",),
        run=lambda message: {"status": "sent", "message": message},
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response("fake_send_message", {"message": "hello"}),
                no_tool_response("Message sent."),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run("send a fake message", Session(), is_tty=False)

    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 1
    assert result.executed_success_count == 1
    assert 'fake_send_message input: {"message": "hello"}' in result.response_text
    assert '"status": "sent"' in result.response_text
    assert "Message sent." in result.response_text
    assert "fake_send_message" in harness.llm.tool_schema_names
    printed = harness.console_buffer.getvalue()
    assert "Message sent." in printed
    assert "fake_send_message" in printed


def test_generic_cli_style_stdout_is_printed_for_user() -> None:
    tool = RegisteredTool(
        name="fake_gh",
        description="Fake gh.",
        input_schema={
            "type": "object",
            "properties": {"args": {"type": "array", "items": {"type": "string"}}},
            "required": ["args"],
            "additionalProperties": False,
        },
        source="github",
        surfaces=("action",),
        run=lambda args: {
            "ok": True,
            "argv": ["gh", *args],
            "exit_code": 0,
            "stdout": "https://github.com/o/r/issues/1\n",
            "stderr": "",
        },
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM([tool_response("fake_gh", {"args": ["issue", "list"]})])
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run("list issues", Session(), is_tty=False)

    assert result.handled is True
    assert "https://github.com/o/r/issues/1" in result.response_text
    assert "https://github.com/o/r/issues/1" in harness.console_buffer.getvalue()


def test_action_final_text_is_streamed_as_user_facing_response() -> None:
    """When the action agent concludes with prose after tools, show that text.

    Interactive-shell ``handled_without_llm`` used to keep only tool dumps in
    ``response_text`` and never render the agent's final Markdown — so skills
    like architecture audit never surfaced their report.
    """
    tool = RegisteredTool(
        name="fake_scan",
        description="Run a fake scan.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        source="knowledge",
        surfaces=("action",),
        run=lambda: {"ok": True},
    )
    report = "### Executive summary\nScan complete.\n"
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response("fake_scan", {}),
                no_tool_response(report),
            ]
        )
    )
    sink = _OutputSink(harness.console)

    result = ActionTurnRunner(
        output=sink,
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run("run the scan and report", Session(), is_tty=False)

    assert result.handled is True
    assert result.response_text == report.strip()
    assert "Executive summary" in harness.console_buffer.getvalue()
    assert "fake_scan result:" not in result.response_text


def test_tool_response_text_overrides_chatty_final_text() -> None:
    tool = RegisteredTool(
        name="fake_security_fix",
        description="Fix a fake security issue.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        source="github",
        surfaces=("action",),
        run=lambda: {
            "success": False,
            "error_kind": "alert_not_found",
            "error": "No open findings found; no PR was created.",
            "response_text": "No open findings found; no PR was created.",
        },
    )
    chatty = (
        "The fixer could not produce a patch.\n\n"
        "Next steps:\n"
        "1. List alerts.\n"
        "2. Pick a specific alert."
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response("fake_security_fix", {}),
                no_tool_response(chatty),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run("fix security issues", Session(), is_tty=False)

    assert result.handled is True
    assert result.response_text == "No open findings found; no PR was created."
    printed = harness.console_buffer.getvalue()
    assert "No open findings found; no PR was created." in printed
    assert "Next steps" not in printed
    assert "Pick a specific alert" not in result.response_text


def _fake_shell_run_tool() -> RegisteredTool:
    def _run_fake_shell(command: str, quiet: bool = False) -> dict[str, Any]:
        _ = quiet
        return {
            "ok": True,
            "command": command,
            "stdout": f"{command} done",
            "stderr": "",
            "exit_code": 0,
        }

    return RegisteredTool(
        name="shell_run",
        description="Fake shell runner.",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        run=_run_fake_shell,
    )


def _shell_step_response(step: int, command: str) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[
            ToolCall(id=f"call_shell_run_{step}", name="shell_run", input={"command": command})
        ],
        raw_content=None,
    )


def test_multi_step_shell_chain_shows_grounded_closing_summary() -> None:
    """A chained multi-step shell workflow ends with the agent's summary.

    ``shell_run`` results carry stdout/exit codes back to the model, so after a
    sequential chain the closing summary is grounded in observed output. It
    used to be dropped with the generic self-recording suppression, leaving
    workflow turns to end silently on the last step's raw output.
    """
    summary = (
        "All 2 steps complete — running total persisted after each step; "
        "checkpoint saved to /tmp/demo_state.json."
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                _shell_step_response(1, "step-1 >> state"),
                _shell_step_response(2, "step-2 >> state"),
                no_tool_response(summary),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(_fake_shell_run_tool()),
        deps=harness.deps,
    ).run("run 2 sequential steps updating a running total", Session(), is_tty=False)

    assert result.handled is True
    assert result.response_text == summary
    # The test console wraps at 100 columns; compare whitespace-normalized.
    printed = " ".join(harness.console_buffer.getvalue().split())
    assert summary in printed


def test_single_shell_run_keeps_model_closing_suppressed() -> None:
    """One shell command still drops the closing so it cannot contradict output."""
    lied = (
        "The disk check passed and everything is completely healthy — no issues found anywhere. ✅"
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                _shell_step_response(1, "df -h"),
                no_tool_response(lied),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(_fake_shell_run_tool()),
        deps=harness.deps,
    ).run("check disk usage", Session(), is_tty=False)

    assert result.handled is True
    assert lied not in result.response_text
    assert lied not in harness.console_buffer.getvalue()


def _fake_slash_invoke_tool() -> RegisteredTool:
    def _run_fake_slash(command: str, args: list[str] | None = None) -> dict[str, Any]:
        line = " ".join([command, *(args or [])])
        return {"ok": True, "output": f"{line} output"}

    return RegisteredTool(
        name="slash_invoke",
        description="Fake slash dispatcher.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        run=_run_fake_slash,
    )


def _slash_step_response(step: int, command: str, args: list[str]) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[
            ToolCall(
                id=f"call_slash_invoke_{step}",
                name="slash_invoke",
                input={"command": command, "args": args},
            )
        ],
        raw_content=None,
    )


def test_discover_then_act_slash_chain_shows_grounded_closing_summary() -> None:
    """A list-then-remove slash chain ends with the agent's grounded summary.

    ``slash_invoke`` observations carry the captured command output back to the
    model (task ids from ``/cron list``), so after a discover-then-act chain the
    closing summary is grounded like a shell chain's — it must not be dropped by
    the generic self-recording suppression.
    """
    summary = "Removed both scheduled cron tasks (ecf7c2580b83, ac9446c4b3eb)."
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                _slash_step_response(1, "/cron", ["list"]),
                _slash_step_response(2, "/cron", ["remove", "ecf7c2580b83"]),
                _slash_step_response(3, "/cron", ["remove", "ac9446c4b3eb"]),
                no_tool_response(summary),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(_fake_slash_invoke_tool()),
        deps=harness.deps,
    ).run("remove the existing cron loops", Session(), is_tty=False)

    assert result.handled is True
    assert result.response_text == summary
    printed = " ".join(harness.console_buffer.getvalue().split())
    assert summary in printed


def test_action_turn_allows_interleaved_slash_repeat() -> None:
    """A → B → A must not lose the final A (batch replay ≠ shared-set membership)."""
    runs: list[str] = []

    def _run_counting(command: str, args: list[str] | None = None) -> dict[str, Any]:
        line = " ".join([command, *(args or [])])
        runs.append(line)
        return {"ok": True, "output": f"{line} output"}

    tool = RegisteredTool(
        name="slash_invoke",
        description="Fake slash dispatcher.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h1",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="i1",
                            name="slash_invoke",
                            input={"command": "/integrations", "args": ["list"]},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h2",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "check health, list integrations, then check health again",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["/health", "/integrations list", "/health"]


def test_action_turn_suppresses_identical_single_slash_repeat() -> None:
    """Product tradeoff: consecutive identical lone slash batch is suppressed (oracle 203 class)."""
    runs: list[str] = []

    def _run_counting(command: str, args: list[str] | None = None) -> dict[str, Any]:
        line = " ".join([command, *(args or [])])
        runs.append(line)
        return {"ok": True, "output": f"{line} output"}

    tool = RegisteredTool(
        name="slash_invoke",
        description="Fake slash dispatcher.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h1",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h2",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "run /health and then run /health again",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["/health"]


def test_action_turn_suppresses_identical_single_shell_repeat() -> None:
    """Product tradeoff: consecutive identical lone shell batch is suppressed."""
    runs: list[str] = []

    def _run_counting(command: str, quiet: bool = False) -> dict[str, Any]:
        del quiet
        runs.append(command)
        return {"ok": True, "output": command, "exit_code": 0}

    tool = RegisteredTool(
        name="shell_run",
        description="Fake shell.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "quiet": {"type": "boolean"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="s1",
                            name="shell_run",
                            input={"command": "pwd", "quiet": False},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="s2",
                            name="shell_run",
                            input={"command": "pwd", "quiet": False},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "print the working directory twice",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["pwd"]


def test_action_turn_suppresses_duplicate_cli_exec() -> None:
    """Regression for oracle 203: consecutive identical cli_exec batch is suppressed."""
    runs: list[str] = []

    def _run_counting(*, payload: str) -> dict[str, Any]:
        runs.append(payload)
        return {"ok": True, "output": f"opensre {payload}"}

    tool = RegisteredTool(
        name="cli_exec",
        description="Fake CLI runner.",
        input_schema={
            "type": "object",
            "properties": {"payload": {"type": "string"}},
            "required": ["payload"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="cli_exec",
                            input={"payload": "integrations verify --dry-run"},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c2",
                            name="cli_exec",
                            input={"payload": "integrations verify --dry-run"},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "please run opensre integrations verify --dry-run",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["integrations verify --dry-run"]


def test_action_turn_suppresses_third_identical_cli_exec_after_blocked_replay() -> None:
    """Snapshot must survive a suppressed replay so a third identical batch stays blocked."""
    runs: list[str] = []

    def _run_counting(*, payload: str) -> dict[str, Any]:
        runs.append(payload)
        return {"ok": True, "output": f"opensre {payload}"}

    tool = RegisteredTool(
        name="cli_exec",
        description="Fake CLI runner.",
        input_schema={
            "type": "object",
            "properties": {"payload": {"type": "string"}},
            "required": ["payload"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="cli_exec",
                            input={"payload": "integrations verify --dry-run"},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c2",
                            name="cli_exec",
                            input={"payload": "integrations verify --dry-run"},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c3",
                            name="cli_exec",
                            input={"payload": "integrations verify --dry-run"},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "please run opensre integrations verify --dry-run",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["integrations verify --dry-run"]


def test_action_turn_allows_cli_exec_retry_after_failure() -> None:
    """Failed cli_exec does not create a success snapshot — the model may retry."""
    runs: list[str] = []

    def _run_counting(*, payload: str) -> dict[str, Any]:
        runs.append(payload)
        if len(runs) == 1:
            return {"ok": False, "error": "transient", "is_error": True}
        return {"ok": True, "output": f"opensre {payload}"}

    tool = RegisteredTool(
        name="cli_exec",
        description="Fake CLI runner.",
        input_schema={
            "type": "object",
            "properties": {"payload": {"type": "string"}},
            "required": ["payload"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="cli_exec",
                            input={"payload": "integrations verify --dry-run"},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="c2",
                            name="cli_exec",
                            input={"payload": "integrations verify --dry-run"},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "please run opensre integrations verify --dry-run",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == [
        "integrations verify --dry-run",
        "integrations verify --dry-run",
    ]


def test_action_turn_suppresses_duplicate_slash_invoke_pair() -> None:
    """Regression for oracle 202: do not re-run the same slash pair in one turn.

    Models sometimes finish /health + /integrations list, then emit the same
    pair again. The second pair must not execute (history / console stay once).
    """
    runs: list[str] = []

    def _run_counting(command: str, args: list[str] | None = None) -> dict[str, Any]:
        line = " ".join([command, *(args or [])])
        runs.append(line)
        return {"ok": True, "output": f"{line} output"}

    tool = RegisteredTool(
        name="slash_invoke",
        description="Fake slash dispatcher.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h1",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                        ToolCall(
                            id="i1",
                            name="slash_invoke",
                            input={"command": "/integrations", "args": ["list"]},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h2",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                        ToolCall(
                            id="i2",
                            name="slash_invoke",
                            input={"command": "/integrations", "args": ["list"]},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "check the health of my opensre and then show me all connected services",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["/health", "/integrations list"]


def test_action_turn_suppresses_a_third_identical_slash_pair() -> None:
    """Suppressing a replay must not re-arm the guard for the batch after it.

    A blocked batch executes nothing, so a guard that tracked only the last
    *successful* batch would forget the pair and let the third one through.
    """
    # Arrange: the model emits the same successful pair three times running.
    runs: list[str] = []

    def _run_counting(command: str, args: list[str] | None = None) -> dict[str, Any]:
        line = " ".join([command, *(args or [])])
        runs.append(line)
        return {"ok": True, "output": f"{line} output"}

    tool = RegisteredTool(
        name="slash_invoke",
        description="Fake slash dispatcher.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )

    def _identical_pair(attempt: int) -> AgentLLMResponse:
        return AgentLLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id=f"h{attempt}",
                    name="slash_invoke",
                    input={"command": "/health", "args": []},
                ),
                ToolCall(
                    id=f"i{attempt}",
                    name="slash_invoke",
                    input={"command": "/integrations", "args": ["list"]},
                ),
            ],
            raw_content=None,
        )

    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                _identical_pair(1),
                _identical_pair(2),
                _identical_pair(3),
                no_tool_response("done"),
            ]
        )
    )

    # Act.
    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "check the health of my opensre and then show me all connected services",
        Session(),
        is_tty=False,
    )

    # Assert: only the first pair reached the tool.
    assert result.handled is True
    assert runs == ["/health", "/integrations list"]


def test_action_turn_mixed_replay_and_new_action_enters_snapshot() -> None:
    """A new success beside a suppressed replay must join the snapshot.

    Otherwise re-emitting only the new action immediately after is allowed
    and the side effect runs twice.
    """
    runs: list[str] = []

    def _run_counting(command: str, args: list[str] | None = None) -> dict[str, Any]:
        line = " ".join([command, *(args or [])])
        runs.append(line)
        return {"ok": True, "output": f"{line} output"}

    tool = RegisteredTool(
        name="slash_invoke",
        description="Fake slash dispatcher.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h1",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                        ToolCall(
                            id="i1",
                            name="slash_invoke",
                            input={"command": "/integrations", "args": ["list"]},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h2",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                        ToolCall(
                            id="r1",
                            name="slash_invoke",
                            input={"command": "/remote", "args": []},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="r2",
                            name="slash_invoke",
                            input={"command": "/remote", "args": []},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "check health and integrations, then health again with remote, then remote",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["/health", "/integrations list", "/remote"]


def test_action_turn_mixed_batch_allows_later_standalone_of_suppressed_member() -> None:
    """After {A,B} then {A suppressed, C ran}, a later standalone A must still run.

    The mixed-batch snapshot is only {C}; retaining A would block the intentional
    interleaved repeat.
    """
    runs: list[str] = []

    def _run_counting(command: str, args: list[str] | None = None) -> dict[str, Any]:
        line = " ".join([command, *(args or [])])
        runs.append(line)
        return {"ok": True, "output": f"{line} output"}

    tool = RegisteredTool(
        name="slash_invoke",
        description="Fake slash dispatcher.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h1",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                        ToolCall(
                            id="i1",
                            name="slash_invoke",
                            input={"command": "/integrations", "args": ["list"]},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h2",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                        ToolCall(
                            id="r1",
                            name="slash_invoke",
                            input={"command": "/remote", "args": []},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h3",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "check health and integrations, then health with remote, then health again",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["/health", "/integrations list", "/remote", "/health"]


def test_action_turn_same_multi_command_batch_twice_is_out_of_scope() -> None:
    """Product tradeoff: intentional same-batch-twice equals accidental consecutive replay.

    Lone or multi — second identical fully-successful batch is suppressed. Next turn.
    """
    runs: list[str] = []

    def _run_counting(command: str, args: list[str] | None = None) -> dict[str, Any]:
        line = " ".join([command, *(args or [])])
        runs.append(line)
        return {"ok": True, "output": f"{line} output"}

    tool = RegisteredTool(
        name="slash_invoke",
        description="Fake slash dispatcher.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h1",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                        ToolCall(
                            id="i1",
                            name="slash_invoke",
                            input={"command": "/integrations", "args": ["list"]},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h2",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                        ToolCall(
                            id="i2",
                            name="slash_invoke",
                            input={"command": "/integrations", "args": ["list"]},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "run /health and /integrations list, then run that same pair again",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["/health", "/integrations list"]


def test_literal_slash_command_dispatches_deterministically_without_llm(
    monkeypatch,
) -> None:
    """A literal ``/command`` typed by the user dispatches via ``slash_invoke``
    without consulting the action-agent LLM, so slash commands keep working when
    the LLM is unavailable (e.g. a provider with no credit)."""
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))
    session = Session()

    result = run_action_tool_turn(
        "/sessions",
        session,
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert result.planned_count == 1
    assert dispatched == ["/sessions"]
    assert session.history == [{"type": "slash", "text": "/sessions", "ok": True}]
    # The deterministic path must not consult the action-agent LLM.
    assert harness.llm.invocations == 0


def test_literal_slash_command_forwards_args_without_llm(monkeypatch) -> None:
    """``/login chatgpt`` dispatches with its positional args and no LLM call."""
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))

    result = run_action_tool_turn(
        "/login chatgpt",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is True
    assert dispatched == ["/login chatgpt"]
    assert harness.llm.invocations == 0


def test_natural_language_still_routes_through_action_agent(monkeypatch) -> None:
    """Non-slash, free-form text is still selected by the action-agent LLM —
    the deterministic path is limited to literal ``/command`` input."""

    def _unexpected_dispatch(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("free-form text must not deterministically dispatch a slash command")

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _unexpected_dispatch)
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))

    result = run_action_tool_turn(
        "log me in please",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert harness.llm.invocations == 1
    assert result.handled is False


def test_execute_with_harness_hands_off_handoff_only_tool_call() -> None:
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [tool_response("assistant_handoff", {"content": "docs:help"})],
        )
    )

    result = run_action_tool_turn(
        "half actionable prompt",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is False
    assert result.has_unhandled_clause is False
    assert result.planned_count == 0
    assert result.handoff_contents == ("docs:help",)
    assert "Requested actions" not in harness.console_buffer.getvalue()


def test_local_llama_handoff_populates_handoff_contents() -> None:
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [tool_response("assistant_handoff", {"content": "provider:local_llama_connect"})],
        )
    )

    result = run_action_tool_turn(
        "please connect to local llama",
        Session(),
        harness.console,
        deps=harness.deps,
    )

    assert result.handled is False
    assert result.handoff_contents == ("provider:local_llama_connect",)


def test_route_handoff_skips_handled_without_llm() -> None:
    from core.agent_harness.turns.orchestrator import TurnRoutingInput, _route_turn

    routing = TurnRoutingInput(
        action_handled=True,
        executed_success_count=1,
        has_observation=False,
    )
    route = _route_turn(routing, handoff_contents=("provider:local_llama_connect",))
    assert route.intent == "gather_and_answer"


def test_route_handled_without_handoff_stays_action_only() -> None:
    from core.agent_harness.turns.orchestrator import TurnRoutingInput, _route_turn

    routing = TurnRoutingInput(
        action_handled=True,
        executed_success_count=1,
        has_observation=False,
    )
    route = _route_turn(routing)
    assert route.intent == "handled_without_llm"


def test_route_investigation_dispatch_skips_gather_even_with_handoff() -> None:
    from core.agent_harness.turns.orchestrator import TurnRoutingInput, _route_turn

    routing = TurnRoutingInput(
        action_handled=True,
        executed_success_count=1,
        has_observation=False,
        investigation_dispatched=True,
    )
    route = _route_turn(
        routing,
        handoff_contents=("chat:non_actionable_literal",),
    )
    assert route.intent == "handled_without_llm"


def test_run_turn_passes_handoff_contents_to_assistant() -> None:
    captured: list[tuple[str, ...]] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
            handoff_contents=("provider:local_llama_connect",),
        )

    def _answer(_text: str, request: AnswerRequest, **_kwargs: Any) -> None:
        captured.append(request.handoff_contents)
        return None

    run_turn(
        "please connect to local llama",
        Session(),
        execute_actions=_execute,
        gather=lambda *_args, **_kwargs: None,
        answer=_answer,
        accounting=DefaultTurnAccounting(Session(), "please connect to local llama"),
    )

    assert captured == [("provider:local_llama_connect",)]


def test_stage_turn_error_routes_to_terminal_facet() -> None:
    """Structured error staging lives on session.terminal; stage_turn_error must reach it."""
    from core.agent_harness.turns.orchestrator import stage_turn_error

    session = Session()
    stage_turn_error(session, "provider_error", "boom")

    assert session.terminal.pop_pending_turn_error() == ("provider_error", "boom")


def test_pop_turn_outcome_hint_reads_terminal_facet() -> None:
    """Outcome hint lives on session.terminal; the driver helper must pop it from there."""
    from core.agent_harness.turns.action_driver import _pop_turn_outcome_hint

    session = Session()
    session.terminal.set_turn_outcome_hint("handled")

    assert _pop_turn_outcome_hint(session) == "handled"
    assert _pop_turn_outcome_hint(session) == ""


def test_run_turn_mixed_action_and_handoff_routes_to_assistant() -> None:
    """Handled action plus handoff tags must not take the handled_without_llm path."""
    captured: list[tuple[str, ...]] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="ran /health",
            handoff_contents=("provider:local_llama_connect",),
        )

    def _answer(_text: str, request: AnswerRequest, **_kwargs: Any) -> None:
        captured.append(request.handoff_contents)
        return None

    result = run_turn(
        "check health and connect local llama",
        Session(),
        execute_actions=_execute,
        gather=lambda *_args, **_kwargs: None,
        answer=_answer,
        accounting=DefaultTurnAccounting(Session(), "check health and connect local llama"),
    )

    assert captured == [("provider:local_llama_connect",)]
    assert result.final_intent == "cli_agent_fallback"


def test_run_turn_skips_gather_for_follow_up_handoff_with_prior_state() -> None:
    """Prior-investigation follow-ups must answer from last_state, not live tools."""
    session = Session()
    session.last_state = {
        "root_cause": "disk full on orders-api",
        "investigation_started_at": time.monotonic(),
    }
    gather_calls: list[str] = []
    answer_kwargs: list[dict[str, Any]] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            handoff_contents=("follow_up:prior_investigation",),
        )

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: search_sentry_issues\nArguments: {}\nResult: should-not-run"

    def _answer(_text: str, request: AnswerRequest, **_kwargs: Any) -> None:
        answer_kwargs.append(
            {
                "tool_observation": request.tool_observation,
                "handoff_contents": request.handoff_contents,
                "defer_want_me_to_closer": request.defer_want_me_to_closer,
            }
        )
        return None

    result = run_turn(
        "what happened?",
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, "what happened?"),
    )

    assert gather_calls == []
    assert answer_kwargs
    assert answer_kwargs[0].get("tool_observation") is None
    assert answer_kwargs[0].get("handoff_contents") == ("follow_up:prior_investigation",)
    # No gather-closer rewrite on follow-ups — do not defer Want-me-to paint.
    assert answer_kwargs[0].get("defer_want_me_to_closer") is False
    assert result.final_intent == "cli_agent_fallback"


def test_database_query_handoff_keeps_connect_want_me_to_closer() -> None:
    """Oracle 332: database_query handoffs must not force investigate Want-me-to.

    ``finalize_gather_investigation_offer`` rewrites any Want-me-to to
    "run a full investigation". For a named MySQL/MCP query with no integrations,
    the correct closer is connect/setup guidance — leave the model's offer alone.
    """
    session = Session()
    prompt = (
        "Use the MySQL tool (ID: mysql-fcf94503) to query the current number of active connections."
    )
    model_reply = (
        "I can't run that MySQL active-connections query because the tool is "
        "not connected in this session.\n\n"
        "**Want me to:** walk you through `/mcp connect mysql`?"
    )

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            handoff_contents=("database_query:mysql_active_connections",),
        )

    def _answer(_text: str, request: AnswerRequest, **_kwargs: Any) -> Any:
        assert request.handoff_contents == ("database_query:mysql_active_connections",)
        return type("Run", (), {"response_text": model_reply})()

    result = run_turn(
        prompt,
        session,
        execute_actions=_execute,
        gather=lambda *_a, **_k: "",
        answer=_answer,
        accounting=DefaultTurnAccounting(session, prompt),
    )

    assert "mysql" in result.assistant_response_text.lower()
    assert "active" in result.assistant_response_text.lower()
    assert "/mcp connect mysql" in result.assistant_response_text
    assert "run a full investigation" not in result.assistant_response_text.lower()
    assert first_pending_offer(session) is None


def test_follow_up_answer_with_want_me_to_paints_on_non_tty_console() -> None:
    """Regression for oracle 600: deferred Want-me-to held the whole non-TTY paint.

    Follow-ups skip ``finalize_gather_investigation_offer`` (no finish flush). If
    ``defer_want_me_to_closer`` stays true and the model adds a Want-me-to closer,
    ``stream_to_console_state`` holds the entire answer on ``force_terminal=False``
    and the console oracle sees an empty response.
    """
    import io
    from types import SimpleNamespace

    from surfaces.interactive_shell.runtime.agent_harness_adapters import ShellOutputSink

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, width=100)
    sink = ShellOutputSink(console)
    session = Session()
    session.last_state = {
        "root_cause": "disk full on orders-api",
        "investigation_started_at": time.monotonic(),
    }

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            handoff_contents=("follow_up:prior_investigation",),
        )

    def _answer(_text: str, request: AnswerRequest, **_kwargs: Any) -> Any:
        body = "The disk was full on orders-api.\n\n**Want me to:** run a full investigation"
        painted = sink.stream(
            label="OpenSRE",
            chunks=[body],
            defer_want_me_to_closer=request.defer_want_me_to_closer,
        )
        return SimpleNamespace(response_text=painted)

    result = run_turn(
        "why did it fail?",
        session,
        execute_actions=_execute,
        gather=lambda *_a, **_k: "should-not-gather",
        answer=_answer,
        accounting=DefaultTurnAccounting(session, "why did it fail?"),
        output=sink,
    )

    painted = buf.getvalue()
    assert "disk was full" in painted
    assert result.assistant_response_text
    assert "disk was full" in result.assistant_response_text


def test_run_turn_still_gathers_for_non_follow_up_handoff_with_prior_state() -> None:
    """Non-follow-up handoffs keep the gather path even when last_state exists."""
    session = Session()
    session.last_state = {
        "root_cause": "disk full on orders-api",
        "investigation_started_at": time.monotonic(),
    }
    gather_calls: list[str] = []
    answer_kwargs: list[dict[str, Any]] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            handoff_contents=("provider:local_llama_connect",),
        )

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: x\nArguments: {}\nResult: y"

    def _answer(_text: str, request: AnswerRequest, **_kwargs: Any) -> None:
        answer_kwargs.append({"defer_want_me_to_closer": request.defer_want_me_to_closer})
        return None

    run_turn(
        "connect local llama",
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, "connect local llama"),
    )

    assert gather_calls == ["connect local llama"]
    assert answer_kwargs
    assert answer_kwargs[0].get("defer_want_me_to_closer") is True


def test_execute_with_harness_handles_llm_unavailable() -> None:
    def _raise() -> object:
        raise RuntimeError("action agent unavailable")

    session = Session()
    result = run_action_tool_turn(
        "action agent outage",
        session,
        Console(force_terminal=False),
        deps=ToolCallingDeps(llm_factory=_raise),
    )

    assert result.handled is True
    assert result.has_unhandled_clause is True
    assert result.planned_count == 0
    assert session.cli_agent_messages[-1] == ("assistant", "action agent unavailable")


def test_llm_build_failure_on_conversational_input_stages_llm_failure() -> None:
    """Conversational turn + provider build failure must flush as a failed LLM call."""
    message = "LLM provider 'anthropic' requires ANTHROPIC_API_KEY to be set."

    def _raise() -> object:
        raise RuntimeError(message)

    session = Session()
    run_action_tool_turn(
        "hi",
        session,
        Console(force_terminal=False),
        deps=ToolCallingDeps(llm_factory=_raise),
    )

    assert session.terminal.pop_pending_turn_error() == ("action_agent_error", message)
    # The client never built, so no attempted-model identity could be staged;
    # the recorder reports "unknown" from the error kind alone.
    assert session.terminal.pop_pending_turn_llm() is None


def test_llm_invoke_failure_on_conversational_input_stages_attempted_model() -> None:
    """Invoke-time provider failure stages the attempted model/provider identity."""
    error = "Bedrock model 'us.anthropic.claude-sonnet-4-6' is not available for your account."

    class _FailingInvokeLLM:
        _model = "us.anthropic.claude-sonnet-4-6"
        _provider_label = "Bedrock"

        def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
            return []

        def invoke(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(error)

    client = _FailingInvokeLLM()
    session = Session()
    run_action_tool_turn(
        "hi",
        session,
        Console(force_terminal=False),
        deps=ToolCallingDeps(llm_factory=lambda: client),
    )

    assert session.terminal.pop_pending_turn_error() == ("action_agent_error", error)
    staged = session.terminal.pop_pending_turn_llm()
    assert staged is not None
    assert staged.model == "us.anthropic.claude-sonnet-4-6"
    assert staged.provider == "bedrock"


def test_llm_invoke_failure_uses_built_client_without_second_factory_call() -> None:
    """Invoke-time failure must stage identity from the client that actually failed."""
    error = "Bedrock model unavailable"
    factory_calls = 0

    class _FailingInvokeLLM:
        _model = "us.anthropic.claude-sonnet-4-6"
        _provider_label = "Bedrock"

        def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
            return []

        def invoke(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(error)

    client = _FailingInvokeLLM()

    def _factory() -> _FailingInvokeLLM:
        nonlocal factory_calls
        factory_calls += 1
        return client

    session = Session()
    run_action_tool_turn(
        "hi",
        session,
        Console(force_terminal=False),
        deps=ToolCallingDeps(llm_factory=_factory),
    )

    assert factory_calls == 1
    staged = session.terminal.pop_pending_turn_llm()
    assert staged is not None
    assert staged.model == client._model


def test_llm_failure_on_literal_slash_input_stays_terminal() -> None:
    """Explicit /slash and !shell turns must keep the terminal-action telemetry shape."""
    from core.agent_harness.turns.action_driver import _stage_action_llm_failure

    for terminal_input in ("/help", "!ls -la"):
        session = Session()
        _stage_action_llm_failure(terminal_input, session, client=None, error_text="boom")
        assert session.terminal.pop_pending_turn_error() is None
        assert session.terminal.pop_pending_turn_llm() is None


def test_stream_failure_stages_llm_error_and_identity() -> None:
    """A conversational stream failure stages both the error and the attempted LLM."""
    from core.agent_harness.prompts.assistant import AssistantTurnPrompt
    from core.agent_harness.turns.orchestrator import _stream_response

    class _FailingStreamClient:
        _model = "claude-sonnet-4-6"
        _provider_label = "Anthropic"

        def invoke_stream(self, _prompt: Any) -> Any:
            raise RuntimeError("Anthropic authentication failed.")

    session = Session()
    run = _stream_response(
        client=_FailingStreamClient(),
        turn_prompt=AssistantTurnPrompt(system="", user="hi"),
        output=_OutputSink(Console(force_terminal=False)),
        run_factory=object(),
        error_reporter=None,
        session=session,
    )

    assert run is None
    assert session.terminal.pop_pending_turn_error() == (
        "assistant_error",
        "Anthropic authentication failed.",
    )
    staged = session.terminal.pop_pending_turn_llm()
    assert staged is not None
    assert staged.model == "claude-sonnet-4-6"
    assert staged.provider == "anthropic"


def test_build_action_agent_returns_action_turn_plan() -> None:
    llm = FakeActionLLM([no_tool_response()])
    deps = ToolCallingDeps(llm_factory=lambda: llm)
    session = Session()

    plan = _build_action_agent(
        message="test message",
        session=session,
        agent_tools=[],
        turn_snapshot=None,
        resolved_integrations={},
        deps=deps,
        tool_hooks=None,
        tool_resources={},
        observer=lambda *_args, **_kwargs: None,
    )

    assert isinstance(plan, ActionTurnPlan)
    assert "test message" in plan.user_message
    assert plan.agent is not None


def test_build_action_agent_keeps_conversation_out_of_system() -> None:
    """Ephemeral history prefixes the user turn so the system cache can stick."""
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

    marker = "zzmarker-harness-conversation-must-not-be-system"
    llm = FakeActionLLM([no_tool_response()])
    deps = ToolCallingDeps(llm_factory=lambda: llm)
    snapshot = TurnSnapshot(
        text="follow up",
        conversation_messages=(("user", marker),),
        configured_integrations=("github",),
        configured_integrations_known=True,
        last_state=None,
        last_synthetic_observation_path=None,
        reasoning_effort=None,
    )

    plan = _build_action_agent(
        message="follow up",
        session=Session(),
        agent_tools=[],
        turn_snapshot=snapshot,
        resolved_integrations={},
        deps=deps,
        tool_hooks=None,
        tool_resources={},
        observer=lambda *_args, **_kwargs: None,
    )

    system = plan.agent._system
    assert marker not in system
    assert marker in plan.user_message
    assert "USER MESSAGE (literal): <<<follow up>>>" in plan.user_message


def test_build_action_agent_system_identical_across_conversation_growth() -> None:
    """Regression: growing history must not rewrite the cached system string."""
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

    llm = FakeActionLLM([no_tool_response()])
    deps = ToolCallingDeps(llm_factory=lambda: llm)

    def _plan(messages: list[tuple[str, str]]) -> ActionTurnPlan:
        return _build_action_agent(
            message="follow up",
            session=Session(),
            agent_tools=[],
            turn_snapshot=TurnSnapshot(
                text="follow up",
                conversation_messages=tuple(messages),
                configured_integrations=("github",),
                configured_integrations_known=True,
                last_state=None,
                last_synthetic_observation_path=None,
                reasoning_effort=None,
            ),
            resolved_integrations={},
            deps=deps,
            tool_hooks=None,
            tool_resources={},
            observer=lambda *_args, **_kwargs: None,
        )

    first = _plan([("user", "hello")])
    second = _plan([("user", "hello"), ("assistant", "hi"), ("user", "zzmarker-growth")])
    assert first.agent._system == second.agent._system
    assert "zzmarker-growth" not in second.agent._system
    assert "zzmarker-growth" in second.user_message


def test_bang_shell_bypass_does_not_use_action_envelope() -> None:
    """Explicit !shell must keep the short static system, not the action envelope."""
    llm = FakeActionLLM([no_tool_response()])
    deps = ToolCallingDeps(llm_factory=lambda: llm)
    plan = _build_action_agent(
        message="!echo hello",
        session=Session(),
        agent_tools=[],
        turn_snapshot=None,
        resolved_integrations={},
        deps=deps,
        tool_hooks=None,
        tool_resources={},
        observer=lambda *_args, **_kwargs: None,
    )
    assert plan.agent._system == "Execute the explicit shell_run tool call."
    assert plan.user_message == "!echo hello"


def test_turn_resolved_integrations_trusts_plan_without_reresolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a plan present, the resolved view is read from it — never re-resolved.

    Pins the single-resolve contract for the empty-integrations edge: an empty
    ``{}`` on the plan is authoritative, not a signal to resolve again.
    """
    from dataclasses import replace

    from core.agent_harness.turns.turn_plan import TurnPlan
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot

    def _must_not_run(_session: object) -> dict:
        raise AssertionError("must not re-resolve when the plan is present")

    monkeypatch.setattr(
        "core.agent_harness.turns.action_driver.resolve_and_cache_integrations", _must_not_run
    )
    snapshot = replace(
        TurnSnapshot.from_session("q", Session(), surface="interactive_shell"),
        resolved_integrations={},
    )
    plan = TurnPlan(snapshot=snapshot)

    assert _turn_resolved_integrations(Session(), plan) == {}


def test_run_turn_skips_gather_for_follow_up_even_when_investigation_is_old() -> None:
    """The planner's follow-up tag is not age-gated.

    Gathering here would answer a question about a past incident with current
    integration results.
    """
    session = Session()
    session.last_state = {
        "root_cause": "disk full on orders-api",
        "investigation_started_at": time.monotonic() - PRIOR_INVESTIGATION_RECALL_SECONDS - 1,
    }
    gather_calls: list[str] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            handoff_contents=("follow_up:prior_investigation",),
        )

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: search_sentry_issues\nArguments: {}\nResult: should-not-run"

    run_turn(
        "what happened?",
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=lambda *_a, **_k: None,
        accounting=DefaultTurnAccounting(session, "what happened?"),
    )

    assert gather_calls == []


def test_run_turn_does_not_gather_after_the_action_turn_already_did_the_work() -> None:
    """A handoff explaining a completed turn must not trigger a live sweep.

    Observed: a morning report ran its fetches, composed the briefing, and
    handed off to explain that Slack delivery had failed. Because a handoff was
    present the turn fell through to gather-and-answer, which queried every
    connected integration for 106 seconds — after the user already had their
    answer. The planner now carries the signal explicitly: a handoff emitted
    with ``requires_gather=false`` sets ``handoff_requires_gather=False`` on
    the turn result, and the router skips the sweep. The assistant still
    speaks; it just composes from what the turn produced instead of probing
    Datadog, Kubernetes and Grafana afresh.
    """
    session = Session()
    gather_calls: list[str] = []
    answer_kwargs: list[dict[str, Any]] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=2,
            executed_count=2,
            executed_success_count=2,
            has_unhandled_clause=False,
            handled=True,
            handoff_contents=("Slack webhook is not configured in this environment.",),
            handoff_requires_gather=False,
        )

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: kubernetes_list_pods\nResult: should-not-run"

    def _answer(_text: str, request: AnswerRequest, **_kwargs: Any) -> None:
        answer_kwargs.append({"handoff_contents": request.handoff_contents})
        return None

    run_turn(
        "give me a morning report",
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, "give me a morning report"),
    )

    assert gather_calls == [], "swept integrations after the turn already answered"
    assert answer_kwargs, "the assistant must still explain the handoff"
    assert answer_kwargs[0]["handoff_contents"]


def test_run_turn_still_gathers_after_actions_when_the_handoff_did_not_opt_out() -> None:
    """Completed actions alone do not skip the gather — only an explicit opt-out does.

    Mirror of the answer-only case above: a turn that ran a tool and handed
    off with the default ``requires_gather`` still needs the live gather pass
    (the handoff may be asking the assistant to look something up the action
    tools could not).
    """
    session = Session()
    gather_calls: list[str] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            handoff_contents=("provider:local_llama_connect",),
        )

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: x\nArguments: {}\nResult: y"

    run_turn(
        "connect local llama",
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=lambda *_a, **_k: None,
        accounting=DefaultTurnAccounting(session, "connect local llama"),
    )

    assert gather_calls == ["connect local llama"]


def test_run_turn_still_gathers_when_the_action_turn_executed_nothing() -> None:
    """A handoff with no work behind it is exactly what gathering is for."""
    session = Session()
    gather_calls: list[str] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
            handoff_contents=("why is the orders-api slow?",),
        )

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: search_sentry_issues\nResult: 3 issues"

    run_turn(
        "why is the orders-api slow?",
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=lambda *_a, **_k: None,
        accounting=DefaultTurnAccounting(session, "why is the orders-api slow?"),
    )

    assert gather_calls, "a question with no prior work still needs evidence"


def test_action_turn_suppresses_partial_replay_of_a_succeeded_batch() -> None:
    """A model may re-emit only part of the batch it just ran.

    The guard compares whole batches, so ``{/health, /integrations list}``
    followed by ``{/health}`` is not an equal batch — yet /health has already
    succeeded this turn and running it again is the same duplicate side effect
    oracles 202/203 exist to prevent.
    """
    runs: list[str] = []

    def _run_counting(command: str, args: list[str] | None = None) -> dict[str, Any]:
        line = " ".join([command, *(args or [])])
        runs.append(line)
        return {"ok": True, "output": f"{line} output"}

    tool = RegisteredTool(
        name="slash_invoke",
        description="Fake slash dispatcher.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        parallel_safe=False,
        run=_run_counting,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h1",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                        ToolCall(
                            id="i1",
                            name="slash_invoke",
                            input={"command": "/integrations", "args": ["list"]},
                        ),
                    ],
                    raw_content=None,
                ),
                AgentLLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="h2",
                            name="slash_invoke",
                            input={"command": "/health", "args": []},
                        ),
                    ],
                    raw_content=None,
                ),
                no_tool_response("done"),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_GenericActionToolProvider(tool),
        deps=harness.deps,
    ).run(
        "check the health of my opensre and then show me all connected services",
        Session(),
        is_tty=False,
    )

    assert result.handled is True
    assert runs == ["/health", "/integrations list"]


def test_run_turn_skips_gather_and_answer_when_the_action_turn_was_cancelled() -> None:
    """A cancelled action turn must not fall through to gather or answer.

    The host already owns the terminal message (soft-timeout notice, or the
    user's stop), so sweeping integrations and composing an answer would both
    cost time nobody is waiting for and overwrite that message. This used to be
    enforced by forcing ``handled=True`` on cancel; the turn result now carries
    ``cancelled`` explicitly and the router short-circuits on it.
    """
    # Arrange: an action turn that ran one action, then was cancelled.
    session = Session()
    gather_calls: list[str] = []
    answer_calls: list[str] = []

    def _execute(*_args: object, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=2,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=False,
            cancelled=True,
        )

    def _gather(text: str, *_args: object, **_kwargs: object) -> str:
        gather_calls.append(text)
        return "Tool: kubernetes_list_pods\nResult: should-not-run"

    def _answer(text: str, _request: AnswerRequest, **_kwargs: Any) -> None:
        answer_calls.append(text)
        return None

    # Act.
    result = run_turn(
        "check health and then list integrations",
        session,
        execute_actions=_execute,
        gather=_gather,
        answer=_answer,
        accounting=DefaultTurnAccounting(session, "check health and then list integrations"),
    )

    # Assert: neither stage ran, and the turn reports the cancel.
    assert gather_calls == [], "swept integrations after the user cancelled"
    assert answer_calls == [], "composed an answer after the user cancelled"
    assert result.action_result is not None
    assert result.action_result.cancelled is True
