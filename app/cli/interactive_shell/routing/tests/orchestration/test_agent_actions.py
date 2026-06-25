"""Tests for terminal action execution in the interactive terminal assistant.

The regex-based natural-language intent inference and deterministic action
mapping have been removed; the LLM action planner is now the sole tool selector
for non-command turns. These tests validate action *execution* mechanics, so the
autouse fixture injects a deterministic fake planner that maps the exact
natural-language phrases used here to explicit ``PlannedAction`` plans.
"""

from __future__ import annotations

import io
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import NoReturn
from unittest.mock import MagicMock

import pytest
from rich.console import Console

import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor as action_executor
import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions as agent_actions
import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tools.implementation_tool as implementation_tool
import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tools.llm_provider_tool as llm_provider_tool
import app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tools.slash_tool as slash_tool
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration import (
    intent_parser as intent_parser_module,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    ActionKind,
    PlannedAction,
    default_target_surface,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.llm_action_planner import (
    LlmActionPlanResult,
)
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.tasks import TaskKind, TaskStatus
from app.cli.interactive_shell.shell import execution as shell_execution

_PLANNER_RESULT_PATCH = (
    "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration"
    ".terminal_actions.planning.plan_actions_with_llm_result"
)


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def _action(
    kind: ActionKind,
    content: str,
    position: int = 0,
    *,
    args: dict[str, object] | None = None,
) -> PlannedAction:
    """Build a ``PlannedAction`` as the LLM planner would emit it."""
    return PlannedAction(
        kind=kind,
        content=content,
        position=position,
        source="llm",
        target_surface=default_target_surface(kind),
        args=dict(args) if args else {},
    )


_NITRO_PROMPT = (
    "I want to deploy OpenSRE on a remote EC2 Nitro instance, and then I want to send\n"
    'it an investigation. Can you please deploy the instance and send it "hello world"?'
)


# Deterministic phrase -> (planned actions, has_unhandled_clause) mapping used by the
# fake LLM planner. Reconstructed from each execution test's own assertions and the
# documented phrase mappings of the (now-removed) deterministic mapper.
#
# Semantics enforced by terminal_actions.planning.plan_actions (v0.1 has NO
# planning-stage fail-closed denial — every terminal action is read-only):
#   - ([], *)                -> fall through to chat (handled is False, no history).
#   - ([...], *)             -> execute the listed (non-handoff) actions; the
#                               has_unhandled flag is ignored, so an unmapped clause
#                               never blocks the matched actions from running.
_FAKE_PLANS: dict[str, tuple[list[PlannedAction], bool]] = {
    "check the health of my opensre and then show me all connected services": (
        [_action("slash", "/health"), _action("slash", "/integrations list")],
        False,
    ),
    "switch from the current ollama model to setting the model to anthropic": (
        [_action("llm_provider", "anthropic")],
        False,
    ),
    "please implement /history search": (
        [_action("implementation", "/history search")],
        False,
    ),
    (
        "tell me about what the discord integration can do and then tell me what "
        "datadog services I have connections to"
    ): (
        [_action("slash", "/integrations show datadog")],
        True,
    ),
    (
        "tell me how you are doing AND show me all the services we are connected to "
        "AND then deploy OpenSRE to EC2"
    ): (
        [_action("slash", "/integrations list"), _action("slash", "/remote")],
        True,
    ),
    _NITRO_PROMPT: (
        [_action("slash", "/remote"), _action("investigation", "hello world")],
        False,
    ),
    (
        "tell me which services are connected AND then tell me the current CLI version "
        "AND then deploy to EC2 within 90 seconds"
    ): (
        [
            _action("slash", "/integrations list"),
            _action("slash", "/version"),
            _action("slash", "/remote"),
        ],
        False,
    ),
    "okay launch a simple alert": (
        [_action("sample_alert", "generic")],
        False,
    ),
    "show me which services are connected and after that run a synthetic test RDS database": (
        [
            _action("slash", "/integrations list"),
            _action("synthetic_test", "rds_postgres:001-replication-lag"),
        ],
        False,
    ),
    "run synthetic test 005-failover": (
        [_action("synthetic_test", "rds_postgres:005-failover")],
        False,
    ),
    "kill the syntehtic_test because it is runnign way too long": (
        [_action("task_cancel", "synthetic_test")],
        False,
    ),
    "show me connected services and sing a song": (
        [_action("slash", "/integrations list")],
        True,
    ),
    # Shell phrases — the planner emits the exact command body for the shell tool.
    "run `pwd`": ([_action("shell", "pwd")], False),
    r"run `cd C:\Users\Alice`": ([_action("shell", r"cd C:\Users\Alice")], False),
    r"run `CD C:\Users\Alice`": ([_action("shell", r"CD C:\Users\Alice")], False),
    r"run `cd C:\`": ([_action("shell", "cd C:\\")], False),
    r'run `cd "C:\Users\Alice"`': ([_action("shell", r'cd "C:\Users\Alice"')], False),
    "execute false": ([_action("shell", "false")], False),
    "run `true`": ([_action("shell", "true")], False),
    "run `!echo hello`": ([_action("shell", "!echo hello")], False),
    "run `!cd /tmp`": ([_action("shell", "!cd /tmp")], False),
    "run `!pwd`": ([_action("shell", "!pwd")], False),
    "run `sudo rm -rf /tmp/demo`": ([_action("shell", "sudo rm -rf /tmp/demo")], False),
    "run `ls | wc -l`": ([_action("shell", "ls | wc -l")], False),
    'run cat "/tmp/file with spaces.txt"': (
        [_action("shell", 'cat "/tmp/file with spaces.txt"')],
        False,
    ),
    'run `cat "/tmp/file with spaces.txt"`': (
        [_action("shell", 'cat "/tmp/file with spaces.txt"')],
        False,
    ),
    'run `cat "unterminated`': (
        [_action("shell", 'cat "unterminated')],
        False,
    ),
}


def _llm_plan_result(
    actions: list[PlannedAction],
    *,
    has_unhandled: bool = False,
    policy_trace: tuple[str, ...] = ("fake_planner",),
) -> LlmActionPlanResult:
    return LlmActionPlanResult(
        actions=tuple(actions),
        has_unhandled_clause=has_unhandled,
        policy_trace=policy_trace,
    )


def _fake_planner_result(
    message: str,
    *,
    session: ReplSession | None = None,  # noqa: ARG001
) -> LlmActionPlanResult:
    actions, has_unhandled = _FAKE_PLANS.get(message, ([], False))
    return _llm_plan_result(list(actions), has_unhandled=has_unhandled)


@pytest.fixture(autouse=True)
def _llm_planner_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a deterministic fake LLM planner for execution-mechanics tests.

    The regex/deterministic mapper has been removed; the LLM action planner is
    now the sole tool selector for non-command turns. Most tests in this file
    validate action *execution* mechanics, not planner quality, so they use a
    stable fake planner that looks up explicit ``PlannedAction`` plans by the
    exact message string. Unknown phrases fall through with ``([], False)``.

    Per-test overrides of ``plan_actions_with_llm_result`` run after this
    autouse fixture, so those tests still win.
    """
    monkeypatch.setattr(_PLANNER_RESULT_PATCH, _fake_planner_result)


def test_execute_cli_actions_dispatches_planned_commands(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions(
        "check the health of my opensre and then show me all connected services",
        session,
        console,
    )

    assert handled.handled is True
    assert dispatched == ["/health", "/integrations list"]
    assert session.history == [
        {
            "type": "cli_agent",
            "text": "check the health of my opensre and then show me all connected services",
            "ok": True,
        },
        {"type": "slash", "text": "/health", "ok": True},
        {"type": "slash", "text": "/integrations list", "ok": True},
    ]
    output = buf.getvalue()
    assert output.index("Requested actions") < output.index("$ /health")
    assert output.index("1.") < output.index("$ /health")
    assert output.index("2.") < output.index("$ /health")
    assert "ran /health" in output
    assert "ran /integrations list" in output


def test_execute_cli_actions_skips_remaining_actions_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-action plan: if the user pressed Esc / typed ``/cancel``
    between actions, the per-dispatch cancel event is set on the
    ``StreamingConsole``. The action loop checks ``cancel_requested``
    at the top of each iteration and breaks, so the remaining actions
    in the plan are NOT dispatched.

    Pre-fix, the loop ran every action regardless of cancel state, so
    cancelling a "do A then B" plan still ran B even after the user
    explicitly asked to stop. This pins the new contract that an
    in-flight cancel halts the plan after the current action.
    """
    dispatched: list[str] = []

    class _CancelAfterFirst:
        """Console-shaped object that returns ``cancel_requested=True``
        only AFTER the first action has been dispatched, simulating
        the user hitting Esc / typing ``/cancel`` between actions."""

        def __init__(self, inner: Console, dispatched: list[str]) -> None:
            self._inner = inner
            self._dispatched = dispatched

        @property
        def cancel_requested(self) -> bool:
            return len(self._dispatched) >= 1

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)

    session = ReplSession()
    inner_console, buf = _capture()
    console = _CancelAfterFirst(inner_console, dispatched)
    handled = agent_actions.execute_cli_actions(
        "check the health of my opensre and then show me all connected services",
        session,
        console,  # type: ignore[arg-type]
    )

    assert handled.handled is True
    # Only the first action ran; the second was skipped because the
    # cancel event was set between iterations.
    assert dispatched == ["/health"], (
        f"second action ran despite cancel between iterations: {dispatched}"
    )
    output = buf.getvalue()
    assert "ran /health" in output
    assert "ran /integrations list" not in output
    assert "remaining actions cancelled" in output


def test_execute_cli_actions_falls_through_for_local_llama_request(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)

    session = ReplSession()
    console, _ = _capture()
    handled = agent_actions.execute_cli_actions("please connect to local llama", session, console)

    assert handled.handled is False
    assert dispatched == []
    assert session.history == []


def test_execute_cli_actions_switches_llm_provider(monkeypatch: object) -> None:
    switches: list[str] = []

    def _fake_switch(provider: str, console: Console, model: str | None = None) -> bool:
        assert model is None
        switches.append(provider)
        console.print(f"switched to {provider}")
        return True

    monkeypatch.setattr(llm_provider_tool, "switch_llm_provider", _fake_switch)

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions(
        "switch from the current ollama model to setting the model to anthropic",
        session,
        console,
    )

    assert handled.handled is True
    assert switches == ["anthropic"]
    assert session.history == [
        {
            "type": "cli_agent",
            "text": "switch from the current ollama model to setting the model to anthropic",
            "ok": True,
        },
        {"type": "slash", "text": "/model set anthropic", "ok": True},
    ]
    output = buf.getvalue()
    assert "$ /model set anthropic" in output
    assert "switched to anthropic" in output


def test_execute_cli_actions_records_llm_provider_failure(monkeypatch: object) -> None:
    def _fake_switch(provider: str, console: Console, model: str | None = None) -> bool:
        assert provider == "anthropic"
        assert model is None
        console.print("missing credential")
        return False

    monkeypatch.setattr(llm_provider_tool, "switch_llm_provider", _fake_switch)

    session = ReplSession()
    console, _ = _capture()
    handled = agent_actions.execute_cli_actions(
        "switch from the current ollama model to setting the model to anthropic",
        session,
        console,
    )

    assert handled.handled is True
    assert session.history[-1] == {"type": "slash", "text": "/model set anthropic", "ok": False}


def test_execute_cli_actions_sets_bare_model_for_active_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reasoning_models: list[str] = []

    monkeypatch.setattr(
        _PLANNER_RESULT_PATCH,
        lambda _message, *, session=None: _llm_plan_result(  # noqa: ARG005
            [
                PlannedAction(
                    kind="llm_provider",
                    content="gpt-5.5",
                    position=0,
                    source="llm",
                    target_surface="slash",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        llm_provider_tool,
        "switch_reasoning_model",
        lambda model, console: (reasoning_models.append(model), console.print(model), True)[2],
    )

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions("switch model to gpt 5.5", session, console)

    assert handled.handled is True
    assert reasoning_models == ["gpt-5.5"]
    assert session.history[-1] == {"type": "slash", "text": "/model set gpt-5.5", "ok": True}
    assert "$ /model set gpt-5.5" in buf.getvalue()


def test_execute_cli_actions_runs_implementation_action(monkeypatch: object) -> None:
    calls: list[str] = []

    def _fake_run_implementation(
        request: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> None:
        calls.append(request)
        session.record("implementation", request, ok=True)
        console.print(f"implemented {request}")

    monkeypatch.setattr(
        implementation_tool,
        "run_claude_code_implementation",
        _fake_run_implementation,
    )

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions(
        "please implement /history search", session, console
    )

    assert handled.handled is True
    assert calls == ["/history search"]
    assert session.history == [
        {"type": "cli_agent", "text": "please implement /history search", "ok": True},
        {"type": "implementation", "text": "/history search", "ok": True},
    ]
    output = buf.getvalue()
    assert "implementation" in output
    assert "implemented /history search" in output


def test_execute_cli_actions_answers_discord_then_dispatches_datadog(
    monkeypatch: object,
) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions(
        (
            "tell me about what the discord integration can do and then tell me what "
            "datadog services I have connections to"
        ),
        session,
        console,
    )

    # v0.1 has no planning-stage denial: the matched clause runs and the
    # unmappable "tell me about discord" clause is simply dropped.
    assert handled.handled is True
    assert dispatched == ["/integrations show datadog"]
    assert session.history == [
        {
            "type": "cli_agent",
            "text": (
                "tell me about what the discord integration can do and then tell me what "
                "datadog services I have connections to"
            ),
            "ok": True,
        },
        {"type": "slash", "text": "/integrations show datadog", "ok": True},
    ]
    output = buf.getvalue()
    assert "ran /integrations show datadog" in output
    assert "couldn't safely decide actions" not in output.lower()


def test_compound_prompt_executes_all_supported_tasks(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions(
        (
            "tell me how you are doing AND show me all the services we are connected to "
            "AND then deploy OpenSRE to EC2"
        ),
        session,
        console,
    )

    # The two executable clauses run; the chatty "tell me how you are doing"
    # clause is dropped without failing the turn closed.
    assert handled.handled is True
    assert dispatched == ["/integrations list", "/remote"]
    assert session.history == [
        {
            "type": "cli_agent",
            "text": (
                "tell me how you are doing AND show me all the services we are connected to "
                "AND then deploy OpenSRE to EC2"
            ),
            "ok": True,
        },
        {"type": "slash", "text": "/integrations list", "ok": True},
        {"type": "slash", "text": "/remote", "ok": True},
    ]
    output = buf.getvalue()
    assert "ran /integrations list" in output
    assert "ran /remote" in output
    assert "couldn't safely decide actions" not in output.lower()


def test_nitro_prompt_executes_remote_then_investigation(monkeypatch: object) -> None:
    dispatched: list[str] = []
    investigation_payloads: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    def _fake_run_investigation_for_session(
        *,
        alert_text: str,
        context_overrides: dict[str, object] | None = None,
        cancel_requested: object | None = None,
    ) -> dict[str, object]:
        _ = (context_overrides, cancel_requested)
        investigation_payloads.append(alert_text)
        return {"root_cause": "hello world handled"}

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    import app.cli.investigation as investigation_module

    monkeypatch.setattr(
        investigation_module,
        "run_investigation_for_session",
        _fake_run_investigation_for_session,
    )

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions(_NITRO_PROMPT, session, console)

    assert handled.handled is True
    assert dispatched == ["/remote"]
    assert investigation_payloads == ["hello world"]
    output = buf.getvalue()
    assert "EC2 deployment creates AWS" not in output
    assert "ran /remote" in output
    assert "investigation: hello world" in output
    assert output.index("ran /remote") < output.index("investigation: hello world")


def test_services_version_deploy_prompt_executes_in_order(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions(
        (
            "tell me which services are connected AND then tell me the current CLI version "
            "AND then deploy to EC2 within 90 seconds"
        ),
        session,
        console,
    )

    assert handled.handled is True
    assert dispatched == ["/integrations list", "/version", "/remote"]
    output = buf.getvalue()
    assert output.index("ran /integrations list") < output.index("ran /version")
    assert "EC2 deployment creates AWS" not in output


def test_execute_cli_actions_runs_sample_alert(monkeypatch: object) -> None:
    calls: list[str] = []

    def _fake_run_sample_alert_for_session(
        *,
        template_name: str = "generic",
        context_overrides: dict[str, object] | None = None,
        cancel_requested: object | None = None,
    ) -> dict[str, object]:
        calls.append(template_name)
        assert context_overrides is None
        return {
            "root_cause": "sample failure",
            "problem_md": "sample",
            "is_noise": False,
        }

    import app.cli.investigation as investigation_module

    monkeypatch.setattr(
        investigation_module,
        "run_sample_alert_for_session",
        _fake_run_sample_alert_for_session,
    )

    session = ReplSession()
    console, buf = _capture()

    assert (
        agent_actions.execute_cli_actions("okay launch a simple alert", session, console).handled
        is True
    )
    assert calls == ["generic"]
    assert session.last_state == {
        "root_cause": "sample failure",
        "problem_md": "sample",
        "is_noise": False,
    }
    assert session.history[-1] == {"type": "alert", "text": "sample:generic", "ok": True}
    inv_tasks = [
        t for t in session.task_registry.list_recent(10) if t.kind == TaskKind.INVESTIGATION
    ]
    assert len(inv_tasks) == 1
    assert inv_tasks[0].status == TaskStatus.COMPLETED
    assert inv_tasks[0].result == "sample failure"
    output = buf.getvalue()
    assert "sample alert" in output
    assert "generic" in output


def test_execute_cli_actions_sample_alert_opensre_error_marks_task_failed(
    monkeypatch: object,
) -> None:
    from app.cli.interactive_shell.error_handling.errors import OpenSREError

    def _raise(
        *,
        template_name: str = "generic",
        context_overrides: dict[str, object] | None = None,
        cancel_requested: object | None = None,
    ) -> dict[str, object]:
        raise OpenSREError("sample pipeline blocked")

    import app.cli.investigation as investigation_module

    monkeypatch.setattr(investigation_module, "run_sample_alert_for_session", _raise)

    session = ReplSession()
    console, _ = _capture()
    assert (
        agent_actions.execute_cli_actions("okay launch a simple alert", session, console).handled
        is True
    )
    inv_tasks = [
        t for t in session.task_registry.list_recent(10) if t.kind == TaskKind.INVESTIGATION
    ]
    assert len(inv_tasks) == 1
    assert inv_tasks[0].status == TaskStatus.FAILED
    assert inv_tasks[0].error == "sample pipeline blocked"


def test_execute_cli_actions_lists_all_actions_before_synthetic_rds(monkeypatch: object) -> None:
    dispatched: list[str] = []
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    def _fake_popen(command: list[str], **kwargs: object) -> MagicMock:
        popen_calls.append((command, kwargs))
        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        return proc

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)
    monkeypatch.setattr(action_executor.subprocess, "Popen", _fake_popen)

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions(
        "show me which services are connected and after that run a synthetic test RDS database",
        session,
        console,
    )

    assert handled.handled is True
    assert dispatched == ["/integrations list"]
    assert len(popen_calls) == 1
    assert popen_calls[0][0] == [
        sys.executable,
        "-u",
        "-m",
        "app.cli",
        "tests",
        "synthetic",
        "--scenario",
        "001-replication-lag",
    ]

    assert session.history[:2] == [
        {
            "type": "cli_agent",
            "text": (
                "show me which services are connected and after that run a synthetic test "
                "RDS database"
            ),
            "ok": True,
        },
        {"type": "slash", "text": "/integrations list", "ok": True},
    ]

    for _ in range(100):
        recent = session.task_registry.list_recent(1)
        if recent and recent[0].status != TaskStatus.RUNNING:
            break
        time.sleep(0.01)
    finished = session.task_registry.list_recent(1)[0]
    assert finished.status == TaskStatus.COMPLETED

    synthetic_entry = session.history[-1]
    assert synthetic_entry["type"] == "synthetic_test"
    assert synthetic_entry["ok"] is True
    assert "rds_postgres" in synthetic_entry["text"]
    assert "task:" in synthetic_entry["text"]

    output = buf.getvalue()
    assert output.index("1.") < output.index("$ /integrations list")
    assert output.index("2.") < output.index("$ /integrations list")
    assert "synthetic test rds_postgres:001-replication-lag" in output
    assert output.index("synthetic test") < output.index("$ opensre tests synthetic")
    assert output.index("$ /integrations list") < output.index("$ opensre tests synthetic")


def test_execute_cli_actions_runs_requested_synthetic_scenario(monkeypatch: object) -> None:
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_popen(command: list[str], **kwargs: object) -> MagicMock:
        popen_calls.append((command, kwargs))
        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        return proc

    monkeypatch.setattr(action_executor.subprocess, "Popen", _fake_popen)

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions("run synthetic test 005-failover", session, console)

    assert handled.handled is True
    assert popen_calls[0][0][-2:] == ["--scenario", "005-failover"]
    assert "$ opensre tests synthetic --scenario 005-failover" in buf.getvalue()


def test_execute_cli_actions_cancels_single_running_synthetic_task() -> None:
    session = ReplSession()
    session.trust_mode = True
    task = session.task_registry.create(TaskKind.SYNTHETIC_TEST)
    task.mark_running()
    proc = MagicMock()
    proc.poll.return_value = None
    task.attach_process(proc)

    console, buf = _capture()
    handled = agent_actions.execute_cli_actions(
        "kill the syntehtic_test because it is runnign way too long",
        session,
        console,
    )

    assert handled.handled is True
    assert task.cancel_requested.is_set()
    proc.terminate.assert_called_once()
    assert session.history == [
        {
            "type": "cli_agent",
            "text": "kill the syntehtic_test because it is runnign way too long",
            "ok": True,
        },
        {"type": "slash", "text": f"/cancel {task.task_id}", "ok": True},
    ]
    output = buf.getvalue()
    assert "cancel task" in output
    assert f"$ /cancel {task.task_id}" in output
    assert "stop requested" in output


def test_partial_match_executes_matched_clause_and_drops_unhandled(monkeypatch: object) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)

    session = ReplSession()
    console, buf = _capture()

    # "sing a song" is chatty filler; v0.1 drops it and still runs the matched
    # "/integrations list" clause instead of failing the whole turn closed.
    assert agent_actions.execute_cli_actions(
        "show me connected services and sing a song", session, console
    ).handled
    assert dispatched == ["/integrations list"]
    output = buf.getvalue()
    assert "ran /integrations list" in output
    assert "couldn't safely decide actions" not in output.lower()


def test_execute_cli_actions_falls_through_for_chat() -> None:
    session = ReplSession()
    console, _ = _capture()

    assert agent_actions.execute_cli_actions("hey", session, console).handled is False
    assert session.history == []


def test_execute_cli_actions_runs_shell_command(monkeypatch: object) -> None:
    def _fake_cwd(_: type[Path]) -> PurePosixPath:
        return PurePosixPath("/tmp/project")

    def _fail_run(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for pwd")

    monkeypatch.setattr(action_executor.Path, "cwd", classmethod(_fake_cwd))
    monkeypatch.setattr(shell_execution.subprocess, "run", _fail_run)

    session = ReplSession()
    console, buf = _capture()

    assert agent_actions.execute_cli_actions("run `pwd`", session, console).handled is True
    assert session.history == [
        {"type": "cli_agent", "text": "run `pwd`", "ok": True},
        {"type": "shell", "text": "pwd", "ok": True},
    ]
    output = buf.getvalue()
    assert "$ pwd" in output
    assert "/tmp/project" in output


def test_execute_cli_actions_cd_preserves_windows_paths(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    monkeypatch.setattr(intent_parser_module, "IS_WINDOWS", True)
    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)

    session = ReplSession()
    console, _ = _capture()

    message = r"run `cd C:\Users\Alice`"
    assert agent_actions.execute_cli_actions(message, session, console).handled is True
    assert changed_directories == [Path(r"C:\Users\Alice")]
    assert session.history == [
        {"type": "cli_agent", "text": message, "ok": True},
        {"type": "shell", "text": r"cd C:\Users\Alice", "ok": True},
    ]


def test_execute_cli_actions_cd_routes_case_insensitively(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    def _fail_run(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for CD")

    monkeypatch.setattr(intent_parser_module, "IS_WINDOWS", True)
    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)
    monkeypatch.setattr(shell_execution.subprocess, "run", _fail_run)

    session = ReplSession()
    console, _ = _capture()

    message = r"run `CD C:\Users\Alice`"
    assert agent_actions.execute_cli_actions(message, session, console).handled is True
    assert changed_directories == [Path(r"C:\Users\Alice")]
    assert session.history == [
        {"type": "cli_agent", "text": message, "ok": True},
        {"type": "shell", "text": r"CD C:\Users\Alice", "ok": True},
    ]


def test_execute_cli_actions_cd_handles_trailing_backslash_on_windows(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    monkeypatch.setattr(intent_parser_module, "IS_WINDOWS", True)
    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)

    session = ReplSession()
    console, _ = _capture()

    message = r"run `cd C:\`"
    assert agent_actions.execute_cli_actions(message, session, console).handled is True
    assert changed_directories == [Path("C:\\")]
    assert session.history == [
        {"type": "cli_agent", "text": message, "ok": True},
        {"type": "shell", "text": "cd C:\\", "ok": True},
    ]


def test_execute_cli_actions_cd_strips_quotes_on_windows(monkeypatch: object) -> None:
    changed_directories: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        changed_directories.append(target)

    monkeypatch.setattr(intent_parser_module, "IS_WINDOWS", True)
    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)

    session = ReplSession()
    console, _ = _capture()

    message = r'run `cd "C:\Users\Alice"`'
    assert agent_actions.execute_cli_actions(message, session, console).handled is True
    assert changed_directories == [Path(r"C:\Users\Alice")]
    assert session.history == [
        {"type": "cli_agent", "text": message, "ok": True},
        {"type": "shell", "text": r'cd "C:\Users\Alice"', "ok": True},
    ]


def test_execute_cli_actions_records_shell_failure(monkeypatch: object) -> None:
    completed = subprocess.CompletedProcess(
        args=["false"],
        returncode=2,
        stdout="",
        stderr="nope\n",
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return completed

    monkeypatch.setattr(shell_execution.subprocess, "run", _fake_run)

    session = ReplSession()
    console, buf = _capture()

    assert agent_actions.execute_cli_actions("execute false", session, console).handled is True
    assert calls == [
        (
            ["false"],
            {
                "shell": False,
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": action_executor.SHELL_COMMAND_TIMEOUT_SECONDS,
                "check": False,
            },
        )
    ]
    assert session.history[-1] == {
        "type": "shell",
        "text": "false",
        "ok": False,
        "response_text": "nope\n✗ exit 2",
    }
    output = buf.getvalue()
    assert "nope" in output
    assert "exit 2" in output


def test_execute_cli_actions_shell_command_times_out(monkeypatch: object) -> None:
    def _timeout(cmd: object, **kwargs: object) -> NoReturn:  # pragma: no cover
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=1,
            output="partial out\n",
            stderr="partial err\n",
        )

    monkeypatch.setattr(shell_execution.subprocess, "run", _timeout)

    session = ReplSession()
    console, buf = _capture()

    assert agent_actions.execute_cli_actions("run `true`", session, console).handled is True
    assert session.history[-1] == {
        "type": "shell",
        "text": "true",
        "ok": False,
        "response_text": "command timed out after 120 seconds",
    }
    output = buf.getvalue().lower()
    assert "timed out" in output
    assert "partial out" in output
    assert "partial err" in output


def test_execute_cli_actions_runs_passthrough_with_shell_true(monkeypatch: object) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_run(command: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

    monkeypatch.setattr(shell_execution.subprocess, "run", _fake_run)

    session = ReplSession()
    console, buf = _capture()

    assert agent_actions.execute_cli_actions("run `!echo hello`", session, console).handled is True
    assert calls == [
        (
            "echo hello",
            {
                "shell": True,
                "executable": shell_execution.os.environ.get("SHELL") or None,
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": action_executor.SHELL_COMMAND_TIMEOUT_SECONDS,
                "check": False,
            },
        )
    ]
    assert session.history[-1] == {"type": "shell", "text": "!echo hello", "ok": True}
    output = buf.getvalue()
    assert "explicit shell passthrough enabled" in output
    assert "ok" in output


def test_execute_cli_actions_routes_bang_cd_through_builtin(monkeypatch: object) -> None:
    dirs: list[Path] = []

    def _fake_chdir(target: Path) -> None:
        dirs.append(target)

    def _boom(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for !cd builtin routing")

    monkeypatch.setattr(action_executor.os, "chdir", _fake_chdir)
    monkeypatch.setattr(shell_execution.subprocess, "run", _boom)

    session = ReplSession()
    console, buf = _capture()

    message = "run `!cd /tmp`"
    assert agent_actions.execute_cli_actions(message, session, console).handled is True
    assert dirs == [Path("/tmp")]
    assert session.history[-1] == {"type": "shell", "text": "cd /tmp", "ok": True}
    captured = buf.getvalue()
    assert "explicit shell passthrough enabled" not in captured


def test_execute_cli_actions_routes_bang_pwd_through_builtin(monkeypatch: object) -> None:
    def _fake_cwd(_: type[Path]) -> PurePosixPath:
        return PurePosixPath("/shown")

    def _boom(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("subprocess.run should not be used for !pwd builtin routing")

    monkeypatch.setattr(action_executor.Path, "cwd", classmethod(_fake_cwd))
    monkeypatch.setattr(shell_execution.subprocess, "run", _boom)

    session = ReplSession()
    console, buf = _capture()

    assert agent_actions.execute_cli_actions("run `!pwd`", session, console).handled is True
    assert session.history[-1] == {"type": "shell", "text": "pwd", "ok": True}
    captured = buf.getvalue()
    assert "/shown" in captured
    assert "explicit shell passthrough enabled" not in captured


def test_execute_cli_actions_denies_restricted_shell_command() -> None:
    """Default-allow still enforces the restricted ``deny`` floor (e.g. ``sudo``).

    Under default-allow, mutating commands like ``rm`` run without a
    confirmation prompt, but restricted commands must still be blocked outright
    and recorded as ``ok=False``.
    """
    session = ReplSession()
    console, buf = _capture()

    assert (
        agent_actions.execute_cli_actions("run `sudo rm -rf /tmp/demo`", session, console).handled
        is True
    )
    assert session.history[-1] == {"type": "shell", "text": "sudo rm -rf /tmp/demo", "ok": False}
    output = buf.getvalue()
    assert "blocked" in output.lower()


def test_execute_cli_actions_blocks_ambiguous_shell_operators() -> None:
    session = ReplSession()
    console, buf = _capture()

    assert agent_actions.execute_cli_actions("run `ls | wc -l`", session, console).handled is True
    assert session.history[-1] == {"type": "shell", "text": "ls | wc -l", "ok": False}
    output = buf.getvalue()
    assert "action blocked" in output.lower()
    assert "shell operators" in output


def test_execute_cli_actions_handles_path_with_spaces_run_phrase() -> None:
    session = ReplSession()
    console, buf = _capture()
    result = agent_actions.execute_cli_actions(
        'run cat "/tmp/file with spaces.txt"', session, console
    )
    assert result.handled is True
    assert session.history[-1]["type"] == "shell"
    output = buf.getvalue()
    assert "/tmp/file with spaces.txt" in output


def test_execute_cli_actions_backtick_shell_preserves_space_path_token(monkeypatch: object) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="done\n",
            stderr="",
        )

    monkeypatch.setattr(shell_execution.subprocess, "run", _fake_run)

    session = ReplSession()
    console, _ = _capture()

    assert (
        agent_actions.execute_cli_actions(
            'run `cat "/tmp/file with spaces.txt"`', session, console
        ).handled
        is True
    )
    # On Windows, shlex with posix=False preserves quotes for tokens with spaces.
    # Both Windows and Posix parsers correctly strip outer quotes from tokens
    # following the policy.py _strip_outer_quotes logic.
    expected_path = "/tmp/file with spaces.txt"
    assert calls[0][0] == ["cat", expected_path]


def test_execute_cli_actions_rejects_malformed_shell_input() -> None:
    session = ReplSession()
    console, buf = _capture()

    assert (
        agent_actions.execute_cli_actions('run `cat "unterminated`', session, console).handled
        is True
    )
    assert session.history[-1] == {"type": "shell", "text": 'cat "unterminated', "ok": False}
    output = buf.getvalue()
    assert "action blocked" in output.lower()
    assert "could not parse command" in output


def test_execute_cli_actions_counts_planned_and_executed(monkeypatch: object) -> None:
    captured_planned: list[tuple[int, bool]] = []
    captured_executed: list[tuple[int, int, int]] = []

    monkeypatch.setattr(
        "app.analytics.cli.capture_terminal_actions_planned",
        lambda *, planned_count, has_unhandled_clause: captured_planned.append(
            (planned_count, has_unhandled_clause)
        ),
    )
    monkeypatch.setattr(
        "app.analytics.cli.capture_terminal_actions_executed",
        lambda *, planned_count, executed_count, executed_success_count: captured_executed.append(
            (planned_count, executed_count, executed_success_count)
        ),
    )

    session = ReplSession()
    console, _ = _capture()
    result = agent_actions.execute_cli_actions("run `pwd`", session, console)

    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 1
    assert result.executed_success_count == 1
    assert captured_planned == [(1, False)]
    assert captured_executed == [(1, 1, 1)]


def test_execute_cli_actions_falls_through_when_llm_plan_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _PLANNER_RESULT_PATCH,
        lambda _message, *, session=None: None,  # noqa: ARG005
    )

    session = ReplSession()
    console, buf = _capture()
    handled = agent_actions.execute_cli_actions("check health", session, console)

    # Planner unavailable now hands off to the conversational assistant rather
    # than denying the turn (no planning-stage fail-closed in v0.1).
    assert handled.handled is False
    assert session.history == []
    output = buf.getvalue()
    assert "couldn't safely decide actions" not in output.lower()


def test_execute_cli_actions_executes_matched_clause_ignoring_unhandled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _PLANNER_RESULT_PATCH,
        lambda _message, *, session=None: _llm_plan_result(  # noqa: ARG005
            [_action("slash", "/health")],
            has_unhandled=True,
        ),
    )

    def _fake_dispatch(
        command: str,
        session: ReplSession,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        session.record("slash", command, ok=True)
        console.print(f"ran {command}")
        return True

    monkeypatch.setattr(slash_tool, "dispatch_slash", _fake_dispatch)

    captured_planned: list[tuple[int, bool]] = []
    captured_executed: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        "app.analytics.cli.capture_terminal_actions_planned",
        lambda *, planned_count, has_unhandled_clause: captured_planned.append(
            (planned_count, has_unhandled_clause)
        ),
    )
    monkeypatch.setattr(
        "app.analytics.cli.capture_terminal_actions_executed",
        lambda *, planned_count, executed_count, executed_success_count: captured_executed.append(
            (planned_count, executed_count, executed_success_count)
        ),
    )

    session = ReplSession()
    console, _ = _capture()
    result = agent_actions.execute_cli_actions("check health", session, console)

    # The unhandled flag no longer denies the turn: the matched /health runs.
    assert result.handled is True
    assert result.planned_count == 1
    assert result.executed_count == 1
    assert result.executed_success_count == 1
    assert result.has_unhandled_clause is False
    assert captured_planned == [(1, False)]
    assert captured_executed == [(1, 1, 1)]


def test_execute_cli_actions_bang_prefix_routes_to_shell_bypassing_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """!cmd prefix must be routed deterministically to shell execution without calling
    the LLM planner.  Regression: bare `!cmd` (and multiline `!cmd\\n   args`) was
    passed to the LLM which misidentified it as a pasted snippet and returned
    assistant_handoff instead of shell_run.
    """
    llm_called: list[str] = []

    def _fail_if_called(message: str, *, session: object = None) -> None:  # pragma: no cover
        llm_called.append(message)
        raise AssertionError("LLM planner must not be called for !cmd input")

    monkeypatch.setattr(_PLANNER_RESULT_PATCH, _fail_if_called)

    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_run(command: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(shell_execution.subprocess, "run", _fake_run)

    session = ReplSession()
    console, buf = _capture()

    # Multiline !cmd with internal whitespace — the exact shape the user types.
    handled = agent_actions.execute_cli_actions("!curl\n      wttr.in/London", session, console)

    assert handled.handled is True
    assert llm_called == [], "LLM planner must not have been invoked for !cmd input"
    assert session.history[-1] == {"type": "shell", "text": "!curl wttr.in/London", "ok": True}
    # The executor strips `!` and runs with shell=True.
    assert calls[0][0] == "curl wttr.in/London"
    assert calls[0][1]["shell"] is True
    assert "explicit shell passthrough enabled" in buf.getvalue()


def test_execute_cli_actions_bang_prefix_single_line_routes_to_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-line !cmd routes to shell execution without any LLM involvement."""
    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_run(command: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="out\n", stderr="")

    monkeypatch.setattr(shell_execution.subprocess, "run", _fake_run)

    session = ReplSession()
    console, _ = _capture()

    handled = agent_actions.execute_cli_actions("!echo hello world", session, console)

    assert handled.handled is True
    assert session.history[-1] == {"type": "shell", "text": "!echo hello world", "ok": True}
    assert calls[0][0] == "echo hello world"
    assert calls[0][1]["shell"] is True


def test_execute_cli_actions_handoff_only_plan_falls_through_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pure assistant_handoff LLM plan must not print a 'Requested actions' header.

    Regression: when the planner returned only assistant_handoff, _execute_planned_actions
    was called and printed '● assistant / Requested actions: 1. assistant handoff [reason]'
    before the real LLM reply ran.  The user saw two assistant headers and internal
    planner reasoning that should have been invisible.
    """
    monkeypatch.setattr(
        _PLANNER_RESULT_PATCH,
        lambda _message, *, session=None: _llm_plan_result(  # noqa: ARG005
            [
                PlannedAction(
                    kind="assistant_handoff",
                    content="informational question about current model",
                    position=0,
                )
            ]
        ),
    )

    session = ReplSession()
    console, buf = _capture()
    result = agent_actions.execute_cli_actions("what is our current model?", session, console)

    # Must fall through (not handled) so the caller invokes the LLM for the real reply.
    assert result.handled is False
    assert result.executed_count == 0
    # No "Requested actions" block should appear — the handoff plan is internal state.
    output = buf.getvalue()
    assert "Requested actions" not in output
    assert "assistant handoff" not in output.lower()
