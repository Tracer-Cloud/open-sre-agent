"""WAL storage records: durable tool intents, commits, and sidecar semantics.

The write-ahead property lives in the storage layer: intents must be fsynced
before returning, and neither intents nor commits may perturb the conversation
parent-chain (they are bookkeeping sidecars, like ``trace_span``).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config.constants import paths
from core.agent_harness.session.persistence import jsonl_storage
from core.agent_harness.session.persistence.jsonl_storage import JsonlSessionStorage
from core.agent_harness.session.persistence.memory import InMemorySessionStorage
from core.agent_harness.session.persistence.paths import session_path


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    return tmp_path


def _session(session_id: str = "sess-wal") -> Any:
    return SimpleNamespace(
        session_id=session_id,
        started_at=1_700_000_000.0,
        agent=SimpleNamespace(messages=[]),
        accumulated_context={},
    )


def _records(session_id: str) -> list[dict[str, Any]]:
    lines = session_path(session_id).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_tool_intent_is_fsynced_and_flagged_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The intent must hit disk (fsync) before the tool runs and stay off-tree."""
    fsyncs: list[int] = []
    real_fsync = jsonl_storage.os.fsync

    def counting_fsync(fd: int) -> None:
        fsyncs.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(jsonl_storage.os, "fsync", counting_fsync)

    storage = JsonlSessionStorage()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="run steps")
    assert not fsyncs, "ordinary appends must not pay the fsync"

    entry_id = storage.append_tool_intent(
        session.session_id,
        tool="shell_run",
        arguments={"command": "step-1 >> state"},
        tool_call_id="call_1",
        seq=1,
        user_text="run 2 sequential steps",
    )

    assert entry_id
    assert len(fsyncs) == 1, "the intent write must fsync"
    intent = next(r for r in _records(session.session_id) if r["type"] == "tool_intent")
    assert intent["sidecar"] is True
    assert intent["parent_id"] is None
    assert intent["tool_call_id"] == "call_1"
    assert intent["seq"] == 1
    assert intent["arguments"] == {"command": "step-1 >> state"}
    assert intent["user_text"] == "run 2 sequential steps"


def test_wal_records_never_become_conversation_parents() -> None:
    """Warm-cache path: messages chain across interleaved intents/commits."""
    storage = JsonlSessionStorage()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="q")
    storage.append_tool_intent(
        session.session_id,
        tool="shell_run",
        arguments={"command": "df -h"},
        tool_call_id="call_1",
        seq=1,
    )
    storage.append_tool_call(
        session.session_id,
        tool="shell_run",
        arguments={"command": "df -h"},
        result="ok",
        ok=True,
        source="wal",
        tool_call_id="call_1",
        sidecar=True,
    )
    storage.append_message(session.session_id, role="assistant", content="a")

    records = _records(session.session_id)
    question = next(r for r in records if r.get("role") == "user")
    answer = next(r for r in records if r.get("role") == "assistant")
    assert answer["parent_id"] == question["id"]
    commit = next(r for r in records if r["type"] == "tool_call")
    result = next(r for r in records if r["type"] == "tool_result")
    assert commit["sidecar"] is True
    assert commit["tool_call_id"] == "call_1"
    assert result["tool_call_id"] == "call_1"
    assert result["parent_id"] == commit["id"]


def test_cold_scan_skips_trailing_wal_sidecars() -> None:
    """A fresh instance resuming after trailing WAL rows finds the real tip."""
    storage = JsonlSessionStorage()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="q")
    for seq in range(1, 4):
        storage.append_tool_intent(
            session.session_id,
            tool="shell_run",
            arguments={"command": f"step-{seq}"},
            tool_call_id=f"call_{seq}",
            seq=seq,
        )

    fresh = JsonlSessionStorage()
    fresh.append_message(session.session_id, role="assistant", content="a")

    records = _records(session.session_id)
    question = next(r for r in records if r.get("role") == "user")
    answer = next(r for r in records if r.get("role") == "assistant")
    assert answer["parent_id"] == question["id"]


def test_non_sidecar_tool_call_keeps_existing_tree_semantics() -> None:
    """The integration-gathering call path (no sidecar flag) is unchanged."""
    storage = JsonlSessionStorage()
    session = _session()
    storage.open_session(session)
    storage.append_tool_call(
        session.session_id, tool="grep", arguments={"q": "x"}, result="hit", ok=True
    )
    storage.append_message(session.session_id, role="assistant", content="done")

    records = _records(session.session_id)
    call = next(r for r in records if r["type"] == "tool_call")
    result = next(r for r in records if r["type"] == "tool_result")
    message = next(r for r in records if r["type"] == "message")
    assert "sidecar" not in call
    assert "tool_call_id" not in call
    assert result["parent_id"] == call["id"]
    assert message["parent_id"] == result["id"]


def test_tool_intent_truncates_oversized_arguments() -> None:
    storage = JsonlSessionStorage()
    session = _session()
    storage.open_session(session)
    storage.append_tool_intent(
        session.session_id,
        tool="shell_run",
        arguments={"command": "x" * 10_000},
        tool_call_id="call_big",
        seq=1,
    )

    intent = next(r for r in _records(session.session_id) if r["type"] == "tool_intent")
    serialized = json.dumps(intent["arguments"], ensure_ascii=False)
    assert len(serialized) < 3_000
    assert "truncated" in intent["arguments"]


def test_memory_store_parity_for_wal_records() -> None:
    """The in-memory backend mirrors intent/commit sidecar semantics."""
    storage = InMemorySessionStorage()
    session = _session()
    storage.open_session(session)
    storage.append_message(session.session_id, role="user", content="q")
    storage.append_tool_intent(
        session.session_id,
        tool="slash_invoke",
        arguments={"command": "/cron", "args": ["list"]},
        tool_call_id="call_1",
        seq=1,
    )
    storage.append_tool_call(
        session.session_id,
        tool="slash_invoke",
        arguments={"command": "/cron", "args": ["list"]},
        result="2 tasks",
        ok=True,
        source="wal",
        tool_call_id="call_1",
        sidecar=True,
    )
    storage.append_message(session.session_id, role="assistant", content="a")

    records = storage.read(session.session_id)
    intent = next(r for r in records if r["type"] == "tool_intent")
    commit = next(r for r in records if r["type"] == "tool_call")
    question = next(r for r in records if r.get("role") == "user")
    answer = next(r for r in records if r.get("role") == "assistant")
    assert intent["sidecar"] is True
    assert commit["tool_call_id"] == "call_1"
    assert answer["parent_id"] == question["id"]
