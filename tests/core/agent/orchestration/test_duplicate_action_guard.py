"""Hook-level tests for the duplicate-action guard's snapshot rules.

The end-to-end cases live in ``test_agent_actions_harness.py``, which drives a
whole action turn per case. These drive ``with_duplicate_action_call_guard``
directly so each ``before_tool_batch`` branch — full success, mixed, and
retain — is pinned on its own, along with the argument normalization that
decides whether two calls count as identical.

The guard covers every tool, not only the three local REPL ones it shipped
with: the loop it exists to break — ask, get something unusable, ask again —
belongs to no particular tool.
"""

from __future__ import annotations

from typing import Any

from core.agent import Agent
from core.agent_harness.turns.action_driver import with_duplicate_action_call_guard
from core.execution import ToolExecutionResult
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool_framework.registered_tool import RegisteredTool

# One emitted tool call: name, arguments, and whether the tool would succeed.
_Call = tuple[str, dict[str, Any], bool]


def _request(name: str, payload: dict[str, Any]) -> Any:
    call = ToolCall(id=f"call-{name}", name=name, input=payload)
    return type("Request", (), {"tool_call": call, "arguments": payload})()


def _label(payload: dict[str, Any]) -> str:
    return str(payload.get("command", payload.get("payload", payload.get("pod", ""))))


def _drive(batches: list[list[_Call]]) -> list[str]:
    """Run ``batches`` through the guard and return the calls that executed."""
    hooks = with_duplicate_action_call_guard()
    executed: list[str] = []
    for batch in batches:
        hooks.before_tool_batch([ToolCall(id="c", name=n, input=p) for n, p, _ in batch])
        for name, payload, succeeds in batch:
            request = _request(name, payload)
            decision = hooks.before_tool_call(request)
            if decision is not None and decision.blocked:
                hooks.after_tool_call(request, ToolExecutionResult(content="", is_error=True))
                continue
            executed.append(_label(payload))
            hooks.after_tool_call(
                request, ToolExecutionResult(content="out", is_error=not succeeds)
            )
    return executed


HEALTH: _Call = ("slash_invoke", {"command": "/health", "args": []}, True)
INTEGRATIONS: _Call = ("slash_invoke", {"command": "/integrations", "args": ["list"]}, True)
INTEGRATIONS_FAILS: _Call = ("slash_invoke", {"command": "/integrations", "args": ["list"]}, False)
OTHER_TOOL: _Call = ("fleet_status", {"command": "n/a"}, True)


def test_a_call_that_failed_beside_a_success_may_retry_while_the_success_may_not() -> None:
    """A partly failed batch contributes only its successful calls to the snapshot.

    The failed call never ran to completion, so retrying it is legitimate; the
    one that did succeed must not repeat its side effect.
    """
    # Arrange / Act: /health succeeds, /integrations errors, then both re-emit.
    executed = _drive([[HEALTH, INTEGRATIONS_FAILS], [HEALTH, INTEGRATIONS]])

    # Assert: /health ran once, /integrations got its retry.
    assert executed == ["/health", "/integrations", "/integrations"]


def test_doing_something_else_in_between_lets_the_call_be_repeated() -> None:
    """The interleave rule: only an *immediate* replay is a duplicate.

    A fully successful batch replaces the snapshot, so A -> B -> A runs A twice.
    That is the deliberate limit of a snapshot the width of one batch: "run that
    again after you have looked at something else" is a real request, and it is
    indistinguishable from an accidental replay without reading the user's mind.
    """
    # Arrange / Act: an unrelated tool runs between two identical /health batches.
    executed = _drive([[HEALTH], [OTHER_TOOL], [HEALTH]])

    # Assert.
    assert executed == ["/health", "n/a", "/health"]


def test_shell_quiet_matches_whether_it_arrives_as_a_bool_or_a_string() -> None:
    """A model re-emitting ``quiet`` as ``"true"`` is repeating the same command."""
    # Arrange / Act.
    as_bool: _Call = ("shell_run", {"command": "pwd", "quiet": True}, True)
    as_string: _Call = ("shell_run", {"command": "pwd", "quiet": "true"}, True)
    executed = _drive([[as_bool], [as_string]])

    # Assert: the second spelling is recognized as the same call.
    assert executed == ["pwd"]


def test_cli_payload_matches_across_surrounding_whitespace() -> None:
    """Padding around a cli_exec payload does not make it a different command."""
    # Arrange / Act.
    plain: _Call = ("cli_exec", {"payload": "integrations verify"}, True)
    padded: _Call = ("cli_exec", {"payload": "  integrations verify  "}, True)
    executed = _drive([[plain], [padded]])

    # Assert.
    assert executed == ["integrations verify"]


def test_identical_cli_exec_twice_in_one_batch_runs_once() -> None:
    """Same-batch duplicates must not wait for the next batch boundary to suppress."""
    first: _Call = ("cli_exec", {"payload": "integrations verify --dry-run"}, True)
    second: _Call = ("cli_exec", {"payload": "integrations verify --dry-run"}, True)
    executed = _drive([[first, second]])

    assert executed == ["integrations verify --dry-run"]


def test_a_read_tool_replaying_the_same_fetch_runs_once() -> None:
    """The incident: one pod's logs fetched over and over until the turn blew up.

    A model that cannot use an oversized result asks for it again. Nothing about
    that loop is specific to the three local REPL tools, so the guard covers
    every tool rather than the ones it originally shipped with.
    """
    # Arrange / Act: the same pod's logs, requested three laps running.
    fetch: _Call = ("kubernetes_get_pod_logs", {"pod": "api-0", "tail_lines": 500}, True)
    executed = _drive([[fetch], [fetch], [fetch]])

    # Assert: fetched once; laps two and three are blocked.
    assert executed == ["api-0"]


def test_a_read_tool_asking_for_something_else_is_not_a_duplicate() -> None:
    """Guarding every tool must not stop the agent working through a list of pods."""
    # Arrange / Act.
    first: _Call = ("kubernetes_get_pod_logs", {"pod": "api-0", "tail_lines": 500}, True)
    other_pod: _Call = ("kubernetes_get_pod_logs", {"pod": "api-1", "tail_lines": 500}, True)
    deeper: _Call = ("kubernetes_get_pod_logs", {"pod": "api-0", "tail_lines": 5000}, True)
    executed = _drive([[first], [other_pod], [deeper]])

    # Assert: a different pod and a different depth are both different work.
    assert executed == ["api-0", "api-1", "api-0"]


def test_reordered_arguments_are_the_same_call() -> None:
    """Providers do not promise key order, so identity cannot depend on it."""
    # Arrange / Act: the same two arguments, emitted the other way round.
    one: _Call = ("kubernetes_get_pod_logs", {"pod": "api-0", "tail_lines": 500}, True)
    same: _Call = ("kubernetes_get_pod_logs", {"tail_lines": 500, "pod": "api-0"}, True)
    executed = _drive([[one], [same]])

    # Assert.
    assert executed == ["api-0"]


def test_duplicates_in_a_parallel_batch_are_caught_before_either_result_arrives() -> None:
    """Parallel-safe tools run concurrently, so success cannot be the signal.

    ``core.execution.execute_tool_calls`` runs a batch on a thread pool unless a
    sequential tool forces otherwise, so both copies of a duplicated call can be
    inside ``before_tool_call`` before either has a result. Replayed here as the
    two ``before`` calls the pool makes with no ``after`` in between.
    """
    # Arrange.
    hooks = with_duplicate_action_call_guard()
    name = "kubernetes_get_pod_logs"
    payload = {"pod": "api-0"}
    hooks.before_tool_batch([ToolCall(id=f"c{i}", name=name, input=payload) for i in range(2)])

    # Act: the pool admits both before either finishes.
    first = hooks.before_tool_call(_request(name, payload))
    second = hooks.before_tool_call(_request(name, payload))

    # Assert: the second is blocked on the strength of the claim alone.
    assert first is None
    assert second is not None and second.blocked


def _probe_tool() -> RegisteredTool:
    """One registered tool the replaying LLM can ask for twice."""
    return RegisteredTool(
        name="kubernetes_get_pod_logs",
        description="Read a pod's logs.",
        input_schema={
            "type": "object",
            "properties": {"pod": {"type": "string"}},
            "required": ["pod"],
            "additionalProperties": False,
        },
        source="knowledge",
        surfaces=("action",),
        run=lambda pod: {"lines": [f"{pod} ok"]},
    )


class _ReplayingLLM:
    """Asks for the same pod's logs twice, then answers."""

    model_id = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": tool.name, "parameters": tool.input_schema} for tool in tools]

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        self.calls += 1
        if self.calls > 2:
            return AgentLLMResponse(content="done", tool_calls=[], raw_content=None)
        return AgentLLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id=f"call-{self.calls}",
                    name="kubernetes_get_pod_logs",
                    input={"pod": "api-0"},
                )
            ],
            raw_content=None,
        )

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {"role": "assistant", "content": content, "tool_calls": list(tool_calls)}

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {"role": "tool", "results": list(results)}


def test_the_end_event_says_a_blocked_duplicate_is_not_a_failure() -> None:
    """The flag the timeline reads has to actually be emitted.

    ``core.execution`` returns a call blocked by ``before_tool_call`` as
    ``is_error`` — that is how the model is told to stop asking. A surface that
    paints outcomes would report a failure for the guard doing its job, so the
    end event carries ``suppressed_duplicate`` alongside it. Driven through a
    real ReAct loop, because a flag no loop emits is a flag nothing sets.
    """
    # Arrange.
    events: list[tuple[str, dict[str, Any]]] = []
    agent: Agent[Any] = Agent(
        llm=_ReplayingLLM(),
        system="sys",
        tools=[_probe_tool()],
        resolved_integrations={},
        max_iterations=4,
        tool_hooks=with_duplicate_action_call_guard(),
        on_event=lambda kind, data: events.append((kind, data)),
    )

    # Act.
    agent.run([{"role": "user", "content": "logs please"}])

    # Assert: the first call ran clean; the replay is an error the reader is
    # told not to read as one.
    ends = [data for kind, data in events if kind == "tool_end"]
    assert [(e["is_error"], e["suppressed_duplicate"]) for e in ends] == [
        (False, False),
        (True, True),
    ]
