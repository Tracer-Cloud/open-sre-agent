"""Append-only JSONL session-tree storage."""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.version import get_opensre_version
from core.agent_harness.session.persistence.paths import session_path
from core.agent_harness.session.persistence.ports import CHAT_KINDS, SessionPersistenceSource

_TRIGGER_MAX_CHARS = 200


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


class JsonlSessionStorage:
    """Per-session v2 JSONL writer.

    The first line is a session header. Every following line is an append-only
    tree entry with ``id``, ``parent_id``, ``timestamp``, and ``type``.
    """

    def open_session(self, session: SessionPersistenceSource) -> None:
        with contextlib.suppress(Exception):
            path = session_path(session.session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "type": "session",
                "version": 2,
                "id": session.session_id,
                "created_at": datetime.fromtimestamp(session.started_at, tz=UTC).isoformat(),
                "cwd": str(Path.cwd()),
                "opensre_version": get_opensre_version(),
            }
            with path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_turn(self, session: SessionPersistenceSource, kind: str, text: str) -> None:
        self._append_entry(
            session.session_id,
            "custom_message",
            {
                "custom_type": "turn_stub",
                "kind": kind,
                "text": text,
                "display": False,
            },
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
        system_prompt: str | None = None,
    ) -> None:
        metadata = {
            key: value
            for key, value in {
                "kind": kind,
                "turn_id": turn_id,
                "model": model,
                "provider": provider,
                "latency_ms": latency_ms,
                "system_prompt": system_prompt,
            }.items()
            if value is not None
        }
        self.append_message(session_id, role="user", content=prompt, metadata=metadata)
        if response:
            self.append_message(
                session_id,
                role="assistant",
                content=response,
                metadata=metadata,
            )

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> str:
        return self._append_entry(
            session_id,
            "message",
            {
                "role": role,
                "content": content,
                "metadata": dict(metadata or {}),
            },
            parent_id=parent_id,
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
        call_id = self._append_entry(
            session_id,
            "tool_call",
            {
                "tool": tool,
                "arguments": arguments,
                "source": source,
            },
        )
        self._append_entry(
            session_id,
            "tool_result",
            {
                "tool": tool,
                "ok": ok,
                "content": result,
                "source": source,
            },
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
        return self._append_entry(
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
        return self._append_entry(
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

    def append_custom_message(
        self,
        session_id: str,
        *,
        custom_type: str,
        content: Any,
        display: bool = True,
    ) -> str:
        return self._append_entry(
            session_id,
            "custom_message",
            {"custom_type": custom_type, "content": content, "display": display},
        )

    def append_trace_span(
        self,
        session_id: str,
        *,
        span_kind: str,
        name: str,
        status: str = "ok",
        duration_ms: int | None = None,
        attributes: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> str:
        """Append a product ``trace_span`` for ATM / debug (thread, route, stage, …)."""
        payload: dict[str, Any] = {
            "span_kind": span_kind,
            "name": name,
            "status": status,
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if attributes:
            payload["attributes"] = attributes
        return self._append_entry(
            session_id,
            "trace_span",
            payload,
            parent_id=parent_id,
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
        self._append_entry(
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
        with contextlib.suppress(Exception):
            path = session_path(session.session_id)
            if not path.exists():
                return
            records = self._read_records(path)
            if not records:
                return
            if records[-1].get("type") == "leaf":
                return
            if not self._has_turns(records):
                path.unlink(missing_ok=True)
                return
            if session.accumulated_context:
                self.append_custom_message(
                    session.session_id,
                    custom_type="accumulated_context",
                    content=dict(session.accumulated_context),
                    display=False,
                )
                records = self._read_records(path)
            if session.agent.messages and not any(rec.get("type") == "message" for rec in records):
                for role, content in session.agent.messages:
                    self.append_message(
                        session.session_id,
                        role=role,
                        content=content,
                        metadata={"kind": "chat"},
                    )
                records = self._read_records(path)
            duration_secs = max(
                0,
                int(
                    (
                        datetime.now(UTC) - datetime.fromtimestamp(session.started_at, tz=UTC)
                    ).total_seconds()
                ),
            )
            self._append_entry(
                session.session_id,
                "leaf",
                {
                    "duration_secs": duration_secs,
                    "total_turns": self._count_turns(records),
                    "chat_turns": self._count_chat_turns(records),
                    "investigation_turns": self._count_investigation_turns(records),
                    "ended_at": _now(),
                },
            )

    def reopen_session(self, _session_id: str) -> None:
        # V2 session files are append-only; reopening just means future entries
        # continue from the current leaf.
        return

    def _append_entry(
        self,
        session_id: str,
        entry_type: str,
        payload: dict[str, Any],
        *,
        parent_id: str | None = None,
    ) -> str:
        with contextlib.suppress(Exception):
            path = session_path(session_id)
            if not path.exists():
                return ""
            entry_id = _new_id()
            parent = parent_id if parent_id is not None else self._current_leaf_id(path)
            record = {
                "id": entry_id,
                "parent_id": parent,
                "timestamp": _now(),
                "type": entry_type,
                **{key: value for key, value in payload.items() if value is not None},
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            return entry_id
        return ""

    @staticmethod
    def _read_records(path: Path) -> list[dict[str, Any]]:
        lines = path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for line in lines:
            with contextlib.suppress(json.JSONDecodeError):
                rec = json.loads(line)
                if isinstance(rec, dict):
                    records.append(rec)
        return records

    def _current_leaf_id(self, path: Path) -> str | None:
        records = self._read_records(path)
        for rec in reversed(records):
            if rec.get("type") == "leaf":
                return str(rec.get("parent_id") or "") or None
            if rec.get("type") != "session":
                return str(rec.get("id") or "") or None
        return None

    @staticmethod
    def _has_turns(records: list[dict[str, Any]]) -> bool:
        return any(
            rec.get("type") in {"message", "investigation_result"}
            or (rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub")
            for rec in records
        )

    @staticmethod
    def _count_turns(records: list[dict[str, Any]]) -> int:
        return sum(
            1
            for rec in records
            if rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub"
        )

    @staticmethod
    def _count_chat_turns(records: list[dict[str, Any]]) -> int:
        return sum(
            1
            for rec in records
            if rec.get("type") == "custom_message"
            and rec.get("custom_type") == "turn_stub"
            and rec.get("kind") in CHAT_KINDS
        )

    @staticmethod
    def _count_investigation_turns(records: list[dict[str, Any]]) -> int:
        return sum(
            1
            for rec in records
            if rec.get("type") == "custom_message"
            and rec.get("custom_type") == "turn_stub"
            and rec.get("kind") in {"alert", "incoming_alert"}
        )
