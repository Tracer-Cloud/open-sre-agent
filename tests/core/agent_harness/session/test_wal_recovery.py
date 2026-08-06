"""WAL recovery: dangling-intent scan, note formatting, and repo exposure."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config.constants import paths
from core.agent_harness.session.persistence.jsonl_repo import JsonlSessionRepo
from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from core.agent_harness.session.persistence.wal_recovery import (
    dangling_tool_intents,
    format_recovery_note,
)


def _intent(call_id: str, seq: int, command: str, user_text: str | None = None) -> dict[str, Any]:
    return {
        "id": f"i{seq}",
        "parent_id": None,
        "type": "tool_intent",
        "sidecar": True,
        "tool": "shell_run",
        "arguments": {"command": command},
        "tool_call_id": call_id,
        "seq": seq,
        **({"user_text": user_text} if user_text else {}),
    }


def _commit(call_id: str) -> dict[str, Any]:
    return {
        "id": f"c{call_id}",
        "parent_id": None,
        "type": "tool_call",
        "sidecar": True,
        "tool": "shell_run",
        "tool_call_id": call_id,
        "source": "wal",
    }


def test_dangling_scan_returns_only_uncommitted_intents() -> None:
    records = [
        _intent("call_1", 1, "step-1"),
        _commit("call_1"),
        _intent("call_2", 2, "step-2"),
        _commit("call_2"),
        _intent("call_3", 3, "step-3"),
        # crash: no commit for call_3
    ]

    dangling = dangling_tool_intents(records)

    assert [rec["tool_call_id"] for rec in dangling] == ["call_3"]


def test_dangling_scan_ignores_unrelated_records() -> None:
    records = [
        {"id": "m1", "type": "message", "role": "user", "content": "hi"},
        {"id": "t1", "type": "tool_call", "tool": "grep"},  # gathering row, no id
        {"id": "s1", "type": "trace_span", "name": "invoke"},
    ]

    assert dangling_tool_intents(records) == []


def test_recovery_note_names_the_interrupted_call_and_request() -> None:
    note = format_recovery_note(
        [_intent("call_3", 3, "step-3 >> /tmp/demo_state.json", user_text="run 5 steps")]
    )

    assert note is not None
    assert "shell_run step-3 >> /tmp/demo_state.json" in note
    assert "(step 3)" in note
    assert "'run 5 steps'" in note
    # The behavioral contract: re-discover, never blind-replay.
    assert "re-discover" in note.lower()
    assert format_recovery_note([]) is None


def test_recovery_note_renders_slash_style_command_args() -> None:
    intent = {
        "type": "tool_intent",
        "tool": "slash_invoke",
        "arguments": {"command": "/cron", "args": ["remove", "ac9446c4b3eb"]},
        "tool_call_id": "call_2",
        "seq": 2,
    }

    note = format_recovery_note([intent])

    assert note is not None
    assert "slash_invoke /cron remove ac9446c4b3eb (step 2)" in note


def test_load_session_exposes_dangling_intents_and_keeps_the_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted session resumes with its conversation intact plus the danglers."""
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)

    storage = JsonlSessionStorage()
    session = SimpleNamespace(
        session_id="feed5eed-0000-0000-0000-000000000000",
        started_at=1_700_000_000.0,
        agent=SimpleNamespace(messages=[]),
        accumulated_context={},
    )
    storage.open_session(session)
    storage.append_turn(session, "chat", "run 2 steps")
    storage.append_message(session.session_id, role="user", content="run 2 steps")
    storage.append_message(session.session_id, role="assistant", content="starting")
    storage.append_tool_intent(
        session.session_id,
        tool="shell_run",
        arguments={"command": "step-1"},
        tool_call_id="call_1",
        seq=1,
    )
    storage.append_tool_call(
        session.session_id,
        tool="shell_run",
        arguments={"command": "step-1"},
        result="ok",
        ok=True,
        source="wal",
        tool_call_id="call_1",
        sidecar=True,
    )
    # Trailing intent with no commit — the process died mid step 2.
    storage.append_tool_intent(
        session.session_id,
        tool="shell_run",
        arguments={"command": "step-2"},
        tool_call_id="call_2",
        seq=2,
    )

    data = JsonlSessionRepo().load_session(session.session_id[:8])

    assert data is not None
    dangling = data["dangling_tool_intents"]
    assert [rec["tool_call_id"] for rec in dangling] == ["call_2"]
    # Trailing WAL sidecars must not hijack branch resolution: the resumed
    # conversation still contains the real messages.
    assert ("user", "run 2 steps") in data["cli_agent_messages"]
    assert ("assistant", "starting") in data["cli_agent_messages"]


def test_load_session_reports_no_danglers_for_a_clean_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)

    storage = JsonlSessionStorage()
    session = SimpleNamespace(
        session_id="c1ea0000-0000-0000-0000-000000000000",
        started_at=1_700_000_000.0,
        agent=SimpleNamespace(messages=[]),
        accumulated_context={},
    )
    storage.open_session(session)
    storage.append_turn(session, "chat", "hello")
    storage.append_message(session.session_id, role="user", content="hello")

    data = JsonlSessionRepo().load_session(session.session_id[:8])

    assert data is not None
    assert data["dangling_tool_intents"] == []


def test_note_shape_is_json_serializable_input() -> None:
    """Bounded-args intents (already-truncated dict) format without error."""
    intent = {
        "type": "tool_intent",
        "tool": "shell_run",
        "arguments": {"truncated": json.dumps({"command": "x" * 50})[:40]},
        "tool_call_id": "call_9",
        "seq": 9,
    }

    note = format_recovery_note([intent])

    assert note is not None
    assert "shell_run" in note
