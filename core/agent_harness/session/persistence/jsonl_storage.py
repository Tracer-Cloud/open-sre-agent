"""Append-only JSONL session-tree storage."""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.version import get_opensre_version
from core.agent_harness.session.persistence.paths import session_path
from core.agent_harness.session.persistence.ports import CHAT_KINDS, SessionPersistenceSource

_TRIGGER_MAX_CHARS = 200
# Cold tip scan keeps at most this many trailing bytes resident (then grows by the
# same step only while the window still contains nothing but skippable rows).
_TAIL_SCAN_CHUNK_BYTES = 64 * 1024
# WAL intent records bound their serialized arguments so a huge tool payload
# (e.g. a full script body) never bloats the session log.
_INTENT_ARGS_MAX_CHARS = 2_000
_INTENT_USER_TEXT_MAX_CHARS = 200


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _bounded_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return ``arguments`` as-is when small, else a truncated JSON preview."""
    try:
        serialized = json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"truncated": str(arguments)[:_INTENT_ARGS_MAX_CHARS]}
    if len(serialized) <= _INTENT_ARGS_MAX_CHARS:
        return arguments
    return {"truncated": serialized[:_INTENT_ARGS_MAX_CHARS]}


class JsonlSessionStorage:
    """Per-session v2 JSONL writer.

    The first line is a session header. Every following line is an append-only
    tree entry with ``id``, ``parent_id``, ``timestamp``, and ``type``.

    Conversation tip (leaf) ids are cached in memory so auto-parented appends
    stay O(1) after the first resolve — a full file scan is only the cold-start
    / cache-miss path. The cache is keyed to the file's ``(mtime_ns, size)`` so
    an out-of-band writer invalidates it. ``trace_span`` records never update
    the tip.
    """

    def __init__(self) -> None:
        # (session_id, path) → last conversation entry id (None = header only).
        # The path is part of the key because session files are scope-dependent
        # (bound org homes); a reused id under another home is a different file.
        self._leaf_ids: dict[tuple[str, str], str | None] = {}
        # (session_id, path) → (mtime_ns, size) when the tip was last trusted.
        self._leaf_file_sig: dict[tuple[str, str], tuple[int, int]] = {}

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
            key = (session.session_id, str(path))
            self._leaf_ids[key] = None
            self._leaf_file_sig[key] = self._file_sig(path)

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
        tool_call_id: str | None = None,
        sidecar: bool = False,
    ) -> None:
        """Record one executed tool call (``tool_call`` + ``tool_result``).

        With ``tool_call_id`` set, the ``tool_call`` record doubles as the WAL
        commit for a matching ``tool_intent``. ``sidecar`` keeps both records
        out of the conversation parent-chain (used by the WAL recorder, whose
        records are durability bookkeeping, not conversation).
        """
        call_id = self._append_entry(
            session_id,
            "tool_call",
            {
                "tool": tool,
                "arguments": arguments,
                "source": source,
                "tool_call_id": tool_call_id,
            },
            sidecar=sidecar,
        )
        self._append_entry(
            session_id,
            "tool_result",
            {
                "tool": tool,
                "ok": ok,
                "content": result,
                "source": source,
                "tool_call_id": tool_call_id,
            },
            parent_id=call_id,
            sidecar=sidecar,
        )

    def append_tool_intent(
        self,
        session_id: str,
        *,
        tool: str,
        arguments: dict[str, Any],
        tool_call_id: str,
        seq: int,
        user_text: str | None = None,
    ) -> str:
        """Durably record a tool call about to execute (the WAL intent).

        Written and fsynced *before* the tool runs; a later ``tool_call``
        record with the same ``tool_call_id`` is its commit. An intent with no
        commit after the session ended means the turn was interrupted
        mid-execution — recovery re-discovers state instead of replaying.
        """
        return self._append_entry(
            session_id,
            "tool_intent",
            {
                "tool": tool,
                "arguments": _bounded_arguments(arguments),
                "tool_call_id": tool_call_id,
                "seq": seq,
                "user_text": (user_text or "")[:_INTENT_USER_TEXT_MAX_CHARS] or None,
            },
            durable=True,
            sidecar=True,
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
        """Append a product ``trace_span`` for ATM / debug (thread, route, stage, …).

        Spans are diagnostic sidecar records: without an explicit ``parent_id``
        they stay detached (no leaf lookup), keeping the append O(1) and out of
        the conversation parent-chain.
        """
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
            resolve_parent=False,
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
            # ``records`` is not re-read after the appends below. The counts in
            # the leaf entry only match ``custom_message``/``turn_stub`` records,
            # and the guard below only looks for ``message`` records — neither
            # append can produce either, so a re-parse would return the same
            # answers for the cost of a full pass over the whole transcript.
            if session.accumulated_context:
                self.append_custom_message(
                    session.session_id,
                    custom_type="accumulated_context",
                    content=dict(session.accumulated_context),
                    display=False,
                )
            if session.agent.messages and not any(rec.get("type") == "message" for rec in records):
                for role, content in session.agent.messages:
                    self.append_message(
                        session.session_id,
                        role=role,
                        content=content,
                        metadata={"kind": "chat"},
                    )
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

    def reopen_session(self, session_id: str) -> None:
        # V2 session files are append-only; drop the tip cache so the next
        # auto-parented append re-resolves from disk (resume / multi-writer).
        # A session id may appear under several scope homes — drop them all.
        stale = [key for key in self._leaf_ids if key[0] == session_id]
        for key in stale:
            self._leaf_ids.pop(key, None)
            self._leaf_file_sig.pop(key, None)

    @staticmethod
    def _file_sig(path: Path) -> tuple[int, int]:
        """Cheap fingerprint for tip-cache validity (mtime_ns, size)."""
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)

    def _append_entry(
        self,
        session_id: str,
        entry_type: str,
        payload: dict[str, Any],
        *,
        parent_id: str | None = None,
        resolve_parent: bool = True,
        durable: bool = False,
        sidecar: bool = False,
    ) -> str:
        """Append one record.

        ``durable`` flushes and fsyncs before returning — required for WAL
        intents, whose write-ahead property only holds if the record hits disk
        before the tool executes. ``sidecar`` marks the record as outside the
        conversation parent-chain: it takes no auto-resolved parent, never
        becomes the tip, and the on-disk ``sidecar`` flag lets the cold tail
        scan skip it (same role the ``trace_span`` type plays).
        """
        with contextlib.suppress(Exception):
            path = session_path(session_id)
            if not path.exists():
                return ""
            entry_id = _new_id()
            parent = parent_id
            if parent is None and resolve_parent and not sidecar:
                parent = self._current_leaf_id(session_id, path)
            record = {
                "id": entry_id,
                "parent_id": parent,
                "timestamp": _now(),
                "type": entry_type,
                **({"sidecar": True} if sidecar else {}),
                **{key: value for key, value in payload.items() if value is not None},
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                if durable:
                    fh.flush()
                    os.fsync(fh.fileno())
            if not sidecar:
                self._remember_leaf(
                    session_id,
                    path,
                    entry_type,
                    entry_id,
                    parent=parent,
                )
            # Refresh the sig even for sidecars: the tip is unchanged but the
            # file grew, and a stale sig would force a cold rescan next append.
            self._leaf_file_sig[(session_id, str(path))] = self._file_sig(path)
            return entry_id
        return ""

    def _remember_leaf(
        self,
        session_id: str,
        path: Path,
        entry_type: str,
        entry_id: str,
        *,
        parent: str | None,
    ) -> None:
        """Update the in-memory conversation tip after a successful append."""
        if entry_type == "trace_span":
            return
        key = (session_id, str(path))
        if entry_type == "leaf":
            # Matches ``_scan_leaf_id``: a trailing leaf record is not itself a
            # parent — the tip stays at the leaf's parent (last real entry).
            self._leaf_ids[key] = parent
            return
        self._leaf_ids[key] = entry_id

    @staticmethod
    def _loads_record(line: str) -> dict[str, Any] | None:
        """Parse one JSONL line into a record dict, or ``None`` if unusable."""
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return None
        return rec if isinstance(rec, dict) else None

    @staticmethod
    def _read_records(path: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = JsonlSessionStorage._loads_record(line)
            if rec is not None:
                records.append(rec)
        return records

    def _current_leaf_id(self, session_id: str, path: Path) -> str | None:
        key = (session_id, str(path))
        sig = self._file_sig(path)
        if key in self._leaf_ids and self._leaf_file_sig.get(key) == sig:
            return self._leaf_ids[key]
        leaf = self._scan_leaf_id(path)
        self._leaf_ids[key] = leaf
        self._leaf_file_sig[key] = sig
        return leaf

    @staticmethod
    def _tip_id_from_record(rec: dict[str, Any]) -> tuple[bool, str | None]:
        """Map one JSONL record to tip resolution.

        Returns ``(True, tip)`` when this record ends the scan, or
        ``(False, None)`` to keep scanning older lines — ``trace_span``
        sidecars, records flagged ``sidecar`` (WAL intents/commits), and
        ``session`` headers are never parents. A header-only file resolves to
        ``None`` when the scan exhausts the file.
        """
        rec_type = rec.get("type")
        if rec_type == "trace_span" or rec.get("sidecar"):
            return False, None
        if rec_type == "leaf":
            parent_id = rec.get("parent_id")
            return True, str(parent_id) if parent_id else None
        if rec_type == "session":
            # Header (or a stray concatenated one) is never a parent; keep
            # scanning older lines. A header-only file resolves to None when
            # the scan exhausts the file.
            return False, None
        entry_id = rec.get("id")
        return True, str(entry_id) if entry_id else None

    def _scan_leaf_id(self, path: Path) -> str | None:
        """Cold-path tip resolve, reading the file tail backwards in deltas.

        Each loop iteration reads only the bytes not yet seen (one chunk,
        prepended to the buffer), so total I/O is linear in the trailing bytes
        even when megabytes of ``trace_span`` rows follow the last conversation
        entry. Lines are scanned newest-first and each complete line is parsed
        at most once. Peak memory is the buffered tail, bounded by the file.

        Tip rules (first matching record from the end):
        - ``trace_span`` / ``sidecar``-flagged / ``session``: skip (sidecar / header)
        - ``leaf``: tip is that marker's ``parent_id``
        - anything else: tip is that record's ``id``
        """
        size = path.stat().st_size
        if size <= 0:
            return None
        with path.open("rb") as fh:
            buffer = b""
            start = size
            scanned_low: int | None = None  # buffer offset of oldest scanned line
            while True:
                if start > 0:
                    new_start = max(0, start - _TAIL_SCAN_CHUNK_BYTES)
                    fh.seek(new_start)
                    delta = fh.read(start - new_start)
                    buffer = delta + buffer
                    if scanned_low is not None:
                        scanned_low += len(delta)
                    start = new_start
                if start > 0:
                    first_newline = buffer.find(b"\n")
                    if first_newline < 0:
                        # No complete line in the buffer yet; read further back.
                        continue
                    region_start = first_newline + 1
                else:
                    region_start = 0
                region_end = scanned_low if scanned_low is not None else len(buffer)
                if region_start < region_end:
                    region = buffer[region_start:region_end]
                    for line in reversed(region.decode("utf-8").splitlines()):
                        if not line.strip():
                            continue
                        rec = self._loads_record(line)
                        if rec is None:
                            continue
                        resolved, tip = self._tip_id_from_record(rec)
                        if resolved:
                            return tip
                    scanned_low = region_start
                if start == 0:
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
