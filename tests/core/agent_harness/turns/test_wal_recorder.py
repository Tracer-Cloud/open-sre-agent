"""WAL recorder on the runtime event stream.

The write-ahead property under test: the intent record is on disk (no commit
yet) at the moment the tool body executes, and a turn that dies between Start
and End leaves a dangling intent the recovery scan can find.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from config.constants import paths
from core.agent_harness.session import default_session_storage
from core.agent_harness.session.persistence.paths import session_path
from core.agent_harness.session.persistence.wal_recovery import dangling_tool_intents
from core.agent_harness.turns.action_driver import ActionTurnRunner
from core.agent_harness.turns.wal_recorder import wal_event_recorder, with_wal_recording
from core.events import RuntimeEvent, ToolExecutionEndEvent, ToolExecutionStartEvent
from core.tool_framework.registered_tool import RegisteredTool
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    return tmp_path


def _records(session_id: str) -> list[dict[str, Any]]:
    path = session_path(session_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _open_session() -> Session:
    session = Session()
    default_session_storage().open_session(session)
    return session


def _start(tool: str, args: dict[str, Any], call_id: str = "call_1") -> ToolExecutionStartEvent:
    return ToolExecutionStartEvent(tool_call_id=call_id, tool_name=tool, args=args, iteration=1)


def _end(
    tool: str,
    args: dict[str, Any],
    call_id: str = "call_1",
    *,
    is_error: bool = False,
) -> ToolExecutionEndEvent:
    return ToolExecutionEndEvent(
        tool_call_id=call_id,
        tool_name=tool,
        args=args,
        result={"ok": not is_error},
        is_error=is_error,
        iteration=1,
    )


# ── Unit: the recorder as a plain event callback ─────────────────────────────


def test_start_without_end_leaves_a_dangling_intent() -> None:
    session = _open_session()
    record = wal_event_recorder(session, user_text="run 2 steps")

    record(_start("shell_run", {"command": "step-1"}))
    # Simulated crash: no End event.

    dangling = dangling_tool_intents(_records(session.session_id))
    assert len(dangling) == 1
    assert dangling[0]["tool"] == "shell_run"
    assert dangling[0]["tool_call_id"] == "call_1"
    assert dangling[0]["user_text"] == "run 2 steps"


def test_end_event_commits_the_intent() -> None:
    session = _open_session()
    record = wal_event_recorder(session)

    record(_start("slash_invoke", {"command": "/cron", "args": ["list"]}))
    record(_end("slash_invoke", {"command": "/cron", "args": ["list"]}))

    records = _records(session.session_id)
    assert dangling_tool_intents(records) == []
    commit = next(r for r in records if r["type"] == "tool_call")
    assert commit["tool_call_id"] == "call_1"
    assert commit["source"] == "wal"
    assert commit["sidecar"] is True


def test_failed_tool_still_commits_with_ok_false() -> None:
    """An observed failure is a commit (the outcome is known), not a dangler."""
    session = _open_session()
    record = wal_event_recorder(session)

    record(_start("shell_run", {"command": "false"}))
    record(_end("shell_run", {"command": "false"}, is_error=True))

    records = _records(session.session_id)
    assert dangling_tool_intents(records) == []
    result = next(r for r in records if r["type"] == "tool_result")
    assert result["ok"] is False


def test_assistant_handoff_is_not_logged() -> None:
    session = _open_session()
    record = wal_event_recorder(session)

    record(_start("assistant_handoff", {}))
    record(_end("assistant_handoff", {}))

    records = _records(session.session_id)
    assert not any(r["type"] == "tool_intent" for r in records)
    assert not any(r["type"] == "tool_call" for r in records)


def test_with_wal_recording_still_invokes_the_wrapped_callback() -> None:
    session = _open_session()
    seen: list[RuntimeEvent] = []
    dispatch = with_wal_recording(seen.append, session=session)

    event = _start("shell_run", {"command": "ls"})
    dispatch(event)

    assert seen == [event]
    assert any(r["type"] == "tool_intent" for r in _records(session.session_id))


# ── Integration: the recorder composed into the action turn engine ──────────


class _ToolProvider:
    def __init__(self, tool: RegisteredTool) -> None:
        self._tool = tool

    def action_tools(self, **_kwargs: object) -> list[RegisteredTool]:
        return [self._tool]

    def tool_resources(self) -> dict[str, Any]:
        return {}

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
    ) -> str:
        _ = (label, suppress_if_starts_with)
        text = "".join(chunks)
        self._console.print(text)
        return text


def test_intent_is_on_disk_before_the_tool_body_runs() -> None:
    """Write-ahead through the real action turn engine.

    The probe tool reads the session file *while executing*: its own intent
    must already be durably recorded, and its commit must not exist yet.
    After the turn, the commit closes the intent.
    """
    session = _open_session()
    at_execution: dict[str, Any] = {}

    def _probe_shell(command: str, quiet: bool = False) -> dict[str, Any]:
        _ = quiet
        records = _records(session.session_id)
        at_execution["intents"] = [
            r for r in records if r["type"] == "tool_intent" and r.get("tool") == "shell_run"
        ]
        at_execution["commits"] = [
            r for r in records if r["type"] == "tool_call" and r.get("tool_call_id")
        ]
        return {"ok": True, "command": command, "stdout": "done", "stderr": "", "exit_code": 0}

    tool = RegisteredTool(
        name="shell_run",
        description="Probe shell runner.",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        source="interactive_shell",
        surfaces=("action",),
        run=_probe_shell,
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response("shell_run", {"command": "step-1 >> state"}),
                no_tool_response("Step complete."),
            ]
        )
    )

    result = ActionTurnRunner(
        output=_OutputSink(harness.console),
        tools=_ToolProvider(tool),
        deps=harness.deps,
    ).run("run step 1", session, is_tty=False)

    assert result.handled is True
    assert len(at_execution["intents"]) == 1, "intent must be on disk when the tool runs"
    assert at_execution["intents"][0]["arguments"] == {"command": "step-1 >> state"}
    assert at_execution["commits"] == [], "commit must only land after execution"
    assert dangling_tool_intents(_records(session.session_id)) == []
