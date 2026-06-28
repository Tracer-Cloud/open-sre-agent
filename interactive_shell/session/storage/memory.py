"""In-memory v2 session storage backend."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from interactive_shell.session.types import SessionPersistenceSource

_TRIGGER_MAX_CHARS = 200


def _now() -> str:
    return datetime.now(UTC).isoformat()


class InMemorySessionStorage:
    """SessionStorage backend that stores v2 records in process memory."""

    def __init__(self) -> None:
        self._files: dict[str, list[dict[str, Any]]] = {}

    def read(self, session_id: str) -> list[dict[str, Any]]:
        return [dict(rec) for rec in self._files.get(session_id, [])]

    def open_session(self, session: SessionPersistenceSource) -> None:
        self._files[session.session_id] = [
            {
                "type": "session",
                "version": 2,
                "id": session.session_id,
                "created_at": datetime.fromtimestamp(session.started_at, tz=UTC).isoformat(),
                "cwd": "",
            }
        ]

    def append_turn(self, session: SessionPersistenceSource, kind: str, text: str) -> None:
        self._append(
            session.session_id,
            "custom_message",
            {"custom_type": "turn_stub", "kind": kind, "text": text, "display": False},
        )

    def append_turn_detail(
        self,
        session_id: str,
        kind: str,
        prompt: str,
        *,
        response: str | None = None,
        turn_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        metadata = {
            key: value
            for key, value in {
                "kind": kind,
                "turn_id": turn_id,
                "model": model,
                "provider": provider,
                "latency_ms": latency_ms,
            }.items()
            if value is not None
        }
        self._append(
            session_id, "message", {"role": "user", "content": prompt, "metadata": metadata}
        )
        if response:
            self._append(
                session_id,
                "message",
                {"role": "assistant", "content": response, "metadata": metadata},
            )

    def append_tool_call(
        self,
        session_id: str,
        *,
        tool: str,
        arguments: dict[str, Any],
        result: str,
        ok: bool,
        source: str | None = None,
    ) -> None:
        call_id = self._append(
            session_id,
            "tool_call",
            {"tool": tool, "arguments": arguments, "source": source},
        )
        self._append(
            session_id,
            "tool_result",
            {"tool": tool, "ok": ok, "content": result, "source": source},
            parent_id=call_id,
        )

    def append_tool_update(
        self,
        session_id: str,
        *,
        tool: str,
        update: Any,
        tool_call_id: str | None = None,
    ) -> str:
        return self._append(
            session_id,
            "tool_update",
            {"tool": tool, "update": update, "tool_call_id": tool_call_id},
        )

    def append_compaction(
        self,
        session_id: str,
        *,
        summary: str,
        first_kept_entry_id: str,
        before_chars: int,
        after_chars: int,
        before_tokens: int | None = None,
        after_tokens: int | None = None,
    ) -> str:
        return self._append(
            session_id,
            "compaction",
            {
                "summary": summary,
                "first_kept_entry_id": first_kept_entry_id,
                "before_chars": before_chars,
                "after_chars": after_chars,
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
            },
        )

    def append_investigation_result(
        self,
        session_id: str,
        state: dict[str, Any],
        *,
        trigger: str = "",
    ) -> str:
        investigation_id = uuid.uuid4().hex[:8]
        report = state.get("problem_md") or state.get("slack_message") or state.get("report") or ""
        self._append(
            session_id,
            "investigation_result",
            {
                "investigation_id": investigation_id,
                "completed_at": _now(),
                "trigger": trigger.strip()[:_TRIGGER_MAX_CHARS],
                "root_cause": str(state.get("root_cause") or ""),
                "report": str(report),
                "root_cause_category": str(state.get("root_cause_category") or ""),
                "alert_name": str(state.get("alert_name") or ""),
                "run_id": str(state.get("run_id") or ""),
            },
        )
        return investigation_id

    def flush(self, session: SessionPersistenceSource) -> None:
        records = self._files.get(session.session_id)
        if not records:
            return
        if records[-1].get("type") == "leaf":
            return
        if not any(rec.get("type") != "session" for rec in records):
            del self._files[session.session_id]
            return
        if session.accumulated_context:
            self._append(
                session.session_id,
                "custom_message",
                {
                    "custom_type": "accumulated_context",
                    "content": dict(session.accumulated_context),
                    "display": False,
                },
            )
            records = self._files.get(session.session_id, records)
        if session.agent.messages and not any(rec.get("type") == "message" for rec in records):
            for role, content in session.agent.messages:
                self._append(
                    session.session_id,
                    "message",
                    {"role": role, "content": content, "metadata": {"kind": "chat"}},
                )
            records = self._files.get(session.session_id, records)
        self._append(
            session.session_id,
            "leaf",
            {
                "total_turns": sum(
                    1
                    for rec in records
                    if rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub"
                )
            },
        )

    def reopen_session(self, _session_id: str) -> None:
        return

    def _append(
        self,
        session_id: str,
        entry_type: str,
        payload: dict[str, Any],
        *,
        parent_id: str | None = None,
    ) -> str:
        records = self._files.get(session_id)
        if records is None:
            return ""
        entry_id = uuid.uuid4().hex
        parent = parent_id if parent_id is not None else self._current_leaf(records)
        records.append(
            {
                "id": entry_id,
                "parent_id": parent,
                "timestamp": _now(),
                "type": entry_type,
                **{key: value for key, value in payload.items() if value is not None},
            }
        )
        return entry_id

    @staticmethod
    def _current_leaf(records: list[dict[str, Any]]) -> str | None:
        for rec in reversed(records):
            if rec.get("type") == "leaf":
                return str(rec.get("parent_id") or "") or None
            if rec.get("type") != "session":
                return str(rec.get("id") or "") or None
        return None
